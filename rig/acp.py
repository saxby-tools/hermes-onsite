"""A small Agent Client Protocol client for driving Hermes in tests.

ACP is what editors such as Zed speak to Hermes: JSON-RPC over the
agent's stdin and stdout. It is the one client surface that can be
scripted with no terminal emulation, and it carries Hermes' approval
prompts as plain requests, so a test can answer allow or deny.

Standard library only.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable


PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 120.0

METHOD_PERMISSION = "session/request_permission"
METHOD_UPDATE = "session/update"

UPDATE_TOOL_CALL = "tool_call"
UPDATE_TOOL_RESULT = "tool_call_update"
UPDATE_MESSAGE = "agent_message_chunk"

ALLOW_KINDS = ("allow_once",)
DENY_KINDS = ("reject_once",)


class AcpError(RuntimeError):
    pass


@dataclass
class ToolCall:
    call_id: str
    title: str
    started: float
    finished: float | None = None
    status: str = ""
    text: str = ""

    @property
    def seconds(self) -> float:
        if self.finished is None:
            return float("inf")

        return self.finished - self.started


@dataclass
class Turn:
    """Everything one prompt produced."""

    tools: list[ToolCall] = field(default_factory=list)
    permissions: list[dict] = field(default_factory=list)
    message: str = ""
    stop_reason: str = ""

    def tool_text(self) -> str:
        return "\n".join(tool.text for tool in self.tools)


def content_text(content) -> str:
    """Flatten ACP content, which nests text a few different ways."""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "\n".join(content_text(item) for item in content)

    if not isinstance(content, dict):
        return ""

    if isinstance(content.get("text"), str):
        return content["text"]

    return content_text(content.get("content"))


def pick_option(options: list[dict], kinds: tuple[str, ...]) -> dict:
    for option in options:
        if option.get("kind") in kinds:
            return option

    raise AcpError(f"no option of kind {kinds} in {options}")


def allow(options: list[dict]) -> dict:
    return pick_option(options, ALLOW_KINDS)


def deny(options: list[dict]) -> dict:
    return pick_option(options, DENY_KINDS)


class AcpClient:
    def __init__(self, command: list[str], cwd: Path, log_path: Path):
        self._cwd = cwd
        self._log = open(log_path, "a")
        self._next_id = 0
        self._inbox: queue.Queue = queue.Queue()
        self._session_id = ""

        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
            cwd=str(cwd),
        )

        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self._process.stdout:
            line = line.strip()

            if not line:
                continue

            try:
                self._inbox.put(json.loads(line))
            except ValueError:
                # Anything that is not JSON is stray output. It is
                # kept in the log and otherwise ignored.
                self._log.write(f"non-json: {line}\n")

        self._inbox.put(None)

    def _send(self, message: dict) -> None:
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _request(self, method: str, params: dict) -> int:
        self._next_id += 1

        self._send({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params,
        })

        return self._next_id

    def _next(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise AcpError("timed out waiting for Hermes")

        try:
            message = self._inbox.get(timeout=remaining)
        except queue.Empty:
            raise AcpError("timed out waiting for Hermes") from None

        if message is None:
            raise AcpError("Hermes closed the connection")

        return message

    def _wait_for(self, request_id: int, on_message, timeout: float) -> dict:
        deadline = time.monotonic() + timeout

        while True:
            message = self._next(deadline)

            if message.get("id") != request_id:
                on_message(message)
                continue

            if "method" in message:
                on_message(message)
                continue

            if "error" in message:
                raise AcpError(json.dumps(message["error"]))

            return message.get("result") or {}

    def start(self) -> str:
        """Initialize the connection and open one session."""

        ignore = lambda message: None

        request = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {},
        })
        self._wait_for(request, ignore, DEFAULT_TIMEOUT_SECONDS)

        request = self._request("session/new", {
            "cwd": str(self._cwd),
            "mcpServers": [],
        })
        result = self._wait_for(request, ignore, DEFAULT_TIMEOUT_SECONDS)

        self._session_id = result["sessionId"]

        return self._session_id

    def _answer_permission(self, message: dict, turn: Turn, decide) -> None:
        params = message.get("params") or {}
        option = decide(params.get("options") or [])

        turn.permissions.append({
            "tool": content_text((params.get("toolCall") or {}).get("content")),
            "title": (params.get("toolCall") or {}).get("title", ""),
            "options": [item.get("name") for item in params["options"]],
            "answer": option.get("name"),
        })

        self._send({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "outcome": {
                    "outcome": "selected",
                    "optionId": option["optionId"],
                },
            },
        })

    def _apply_update(self, update: dict, turn: Turn) -> None:
        kind = update.get("sessionUpdate")

        if kind == UPDATE_MESSAGE:
            turn.message += content_text(update.get("content"))
            return

        if kind == UPDATE_TOOL_CALL:
            turn.tools.append(ToolCall(
                call_id=update.get("toolCallId", ""),
                title=update.get("title", ""),
                started=time.monotonic(),
            ))
            return

        if kind != UPDATE_TOOL_RESULT:
            return

        for tool in turn.tools:
            if tool.call_id != update.get("toolCallId"):
                continue

            tool.finished = time.monotonic()
            tool.status = update.get("status", "")
            tool.text = content_text(update.get("content"))

    def prompt(
        self,
        text: str,
        decide: Callable[[list[dict]], dict] = allow,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Turn:
        """Send one prompt and collect what Hermes does with it."""

        turn = Turn()

        def on_message(message: dict) -> None:
            method = message.get("method")

            if method == METHOD_PERMISSION:
                self._answer_permission(message, turn, decide)
                return

            if method != METHOD_UPDATE:
                return

            update = (message.get("params") or {}).get("update") or {}
            self._apply_update(update, turn)

        request = self._request("session/prompt", {
            "sessionId": self._session_id,
            "prompt": [{"type": "text", "text": text}],
        })

        result = self._wait_for(request, on_message, timeout)
        turn.stop_reason = result.get("stopReason", "")

        return turn

    def close(self) -> None:
        try:
            self._process.stdin.close()
            self._process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self._process.kill()

        self._log.close()
