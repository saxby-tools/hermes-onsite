"""A TCP relay the rig can cut or freeze on command.

A real laptop cannot be stopped or paused from a test. So the rig
points the plugin at this relay, and the relay passes every byte on
to the laptop's own SSH port. Then:

- `close()` drops every connection and refuses new ones. That is how
  a machine that is switched off looks.
- `freeze()` keeps every connection open and passes nothing. That is
  how a sleeping laptop looks from the network.

Nothing on the laptop is touched to fake an outage.

Standard library only.
"""

from __future__ import annotations

import select
import socket
import threading
import time


LISTEN_HOST = "127.0.0.1"
LISTEN_BACKLOG = 16
CONNECT_SECONDS = 10
POLL_SECONDS = 0.1
FROZEN_NAP_SECONDS = 0.05
CHUNK_BYTES = 65536


class Relay:
    """One listening port, passed through to one host and port."""

    def __init__(self, listen_port: int, host: str, port: int):
        self.listen_port = listen_port
        self.host = host
        self.port = port

        self._listener: socket.socket | None = None
        self._accepting = threading.Event()
        self._frozen = threading.Event()
        self._acceptor: threading.Thread | None = None
        self._sockets: set[socket.socket] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _shut(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        sock.close()

    def _forget(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(sock)

        self._shut(sock)

    def _moved(self, source: socket.socket, sink: socket.socket) -> bool:
        """Pass one chunk along. False once the source has closed."""

        if self._frozen.is_set():
            time.sleep(FROZEN_NAP_SECONDS)
            return True

        ready, _, _ = select.select([source], [], [], POLL_SECONDS)

        if not ready:
            return True

        # A freeze that lands during the wait above must hold the
        # bytes back, not let one last chunk through.
        if self._frozen.is_set():
            return True

        chunk = source.recv(CHUNK_BYTES)

        if not chunk:
            return False

        sink.sendall(chunk)

        return True

    def _pump(self, source: socket.socket, sink: socket.socket) -> None:
        try:
            while self._moved(source, sink):
                pass
        except (OSError, ValueError):
            # Either end went away, or `close()` took the socket.
            pass

        self._forget(source)
        self._forget(sink)

    def _pair(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(
                (self.host, self.port),
                timeout=CONNECT_SECONDS,
            )
        except OSError:
            self._shut(client)
            return

        upstream.settimeout(None)
        client.settimeout(None)

        with self._lock:
            self._sockets.add(client)
            self._sockets.add(upstream)

        for source, sink in ((client, upstream), (upstream, client)):
            threading.Thread(
                target=self._pump,
                args=(source, sink),
                daemon=True,
            ).start()

    def _accept(self) -> None:
        while self._accepting.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return

            self._pair(client)

    def open(self) -> None:
        if self._listener is not None:
            return

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LISTEN_HOST, self.listen_port))
        listener.listen(LISTEN_BACKLOG)
        listener.settimeout(POLL_SECONDS)

        self._listener = listener
        self._accepting.set()
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()

    def close(self) -> None:
        if self._listener is None:
            return

        # The acceptor is stopped first, so no connection can slip in
        # between closing the old ones and closing the port.
        self._accepting.clear()
        self._acceptor.join()

        self._listener.close()
        self._listener = None

        with self._lock:
            doomed = list(self._sockets)
            self._sockets.clear()

        for sock in doomed:
            self._shut(sock)

    def freeze(self) -> None:
        self._frozen.set()

    def thaw(self) -> None:
        self._frozen.clear()
