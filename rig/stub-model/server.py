"""A stub OpenAI-compatible model for the onsite test rig.

It returns scripted tool calls, so an end-to-end run gives the same
result every time. No real model is involved.

How a script is chosen: the latest user message names it, as in
`script:pwd`. The server then replays `scripts/pwd.json` one step per
request. The step index is the number of tool results that follow the
latest user message, so the server holds no state between requests and
a retried request gets the same answer.

Standard library only. The rig must not need a package index.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11499
DEFAULT_SCRIPTS_DIR = Path(__file__).parent / "scripts"

MODEL_ID = "stub"
LOG_PREVIEW_CHARS = 400
SCRIPT_PREFIX = "script:"
SCRIPT_NAME_RE = re.compile(r"script:([a-z0-9][a-z0-9-]*)")

# Hermes sends side requests with no tools (titles, summaries, memory
# extraction). They get this fixed reply so they never consume a step.
NO_TOOLS_REPLY = "ok"

# `/run N` hands the agent a message that asks it to run a command
# from a code fence. A real model would comply. The stub does the
# same, so the round trip can be tested.
RUN_REQUEST_RE = re.compile(
    r"Run proposed command #\d+ now.*?```\n(.*?)\n```",
    re.DOTALL,
)
NO_SCRIPT_REPLY = "No script named. Say `script:<name>`."
SCRIPT_DONE_REPLY = "Script finished."


def message_text(message: dict) -> str:
    content = message.get("content")

    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return ""

    parts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict)
    ]

    return "\n".join(parts)


def find_last_user_index(messages: list) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index

    return -1


def find_script_name(messages: list) -> str | None:
    index = find_last_user_index(messages)

    if index < 0:
        return None

    match = SCRIPT_NAME_RE.search(message_text(messages[index]))

    if match is None:
        return None

    return match.group(1)


def count_tool_results(messages: list) -> int:
    index = find_last_user_index(messages)
    tail = messages[index + 1:]

    return sum(1 for message in tail if message.get("role") == "tool")


def load_script(scripts_dir: Path, name: str) -> list | None:
    path = scripts_dir / f"{name}.json"

    if not path.is_file():
        return None

    return json.loads(path.read_text())["steps"]


def find_run_request(messages: list) -> str | None:
    index = find_last_user_index(messages)

    if index < 0:
        return None

    match = RUN_REQUEST_RE.search(message_text(messages[index]))

    if match is None:
        return None

    return match.group(1)


def pick_run_step(command: str, messages: list) -> dict:
    if count_tool_results(messages) > 0:
        return {"text": SCRIPT_DONE_REPLY}

    return {"tool": "terminal", "args": {"command": command}}


def pick_step(scripts_dir: Path, body: dict) -> dict:
    messages = body.get("messages") or []

    if not body.get("tools"):
        return {"text": NO_TOOLS_REPLY}

    command = find_run_request(messages)

    if command is not None:
        return pick_run_step(command, messages)

    name = find_script_name(messages)

    if name is None:
        return {"text": NO_SCRIPT_REPLY}

    steps = load_script(scripts_dir, name)

    if steps is None:
        return {"text": f"Unknown script: {name}"}

    index = count_tool_results(messages)

    if index >= len(steps):
        return {"text": SCRIPT_DONE_REPLY}

    return steps[index]


def build_tool_call(step: dict) -> dict:
    return {
        "id": f"call_{uuid.uuid4().hex[:16]}",
        "type": "function",
        "function": {
            "name": step["tool"],
            "arguments": json.dumps(step.get("args", {})),
        },
    }


def build_message(step: dict) -> tuple[dict, str]:
    if "tool" in step:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [build_tool_call(step)],
        }

        return message, "tool_calls"

    message = {"role": "assistant", "content": step.get("text", "")}

    return message, "stop"


def build_usage() -> dict:
    return {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }


def build_completion(step: dict) -> dict:
    message, finish_reason = build_message(step)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            },
        ],
        "usage": build_usage(),
    }


def build_stream_delta(message: dict) -> dict:
    delta = {"role": "assistant"}

    if message.get("content") is not None:
        delta["content"] = message["content"]

    if "tool_calls" not in message:
        return delta

    # A streamed tool call carries its position in the list.
    delta["tool_calls"] = [
        {"index": index, **call}
        for index, call in enumerate(message["tool_calls"])
    ]

    return delta


def build_stream_chunks(step: dict) -> list[dict]:
    message, finish_reason = build_message(step)

    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
    }

    first = {
        **base,
        "choices": [
            {
                "index": 0,
                "delta": build_stream_delta(message),
                "finish_reason": None,
            },
        ],
    }

    last = {
        **base,
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": finish_reason},
        ],
    }

    usage = {**base, "choices": [], "usage": build_usage()}

    return [first, last, usage]


def build_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "created": 0,
                "owned_by": "onsite-rig",
            },
        ],
    }


class StubHandler(BaseHTTPRequestHandler):
    scripts_dir = DEFAULT_SCRIPTS_DIR

    def log_message(self, format, *args):
        sys.stderr.write("stub-model: " + (format % args) + "\n")

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_stream(self, chunks: list[dict]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        for chunk in chunks:
            line = f"data: {json.dumps(chunk)}\n\n"
            self.wfile.write(line.encode())

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)

        if length == 0:
            return {}

        return json.loads(self.rfile.read(length))

    def log_request_summary(self, body: dict, step: dict) -> None:
        """One line per request, so a failed run can be read back."""

        messages = body.get("messages") or []
        index = find_last_user_index(messages)
        asked = message_text(messages[index]) if index >= 0 else ""
        chosen = step.get("tool") or "text"

        self.log_message(
            "tools=%s asked=%r -> %s",
            bool(body.get("tools")),
            asked[:LOG_PREVIEW_CHARS],
            chosen,
        )

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self.send_json(200, build_models())
            return

        self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_json(404, {"error": {"message": "not found"}})
            return

        body = self.read_body()
        step = pick_step(self.scripts_dir, body)

        self.log_request_summary(body, step)

        if body.get("stream"):
            self.send_stream(build_stream_chunks(step))
            return

        self.send_json(200, build_completion(step))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--scripts",
        type=Path,
        default=DEFAULT_SCRIPTS_DIR,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    StubHandler.scripts_dir = args.scripts

    server = ThreadingHTTPServer((args.host, args.port), StubHandler)

    print(
        f"stub-model: listening on http://{args.host}:{args.port}/v1",
        file=sys.stderr,
    )

    server.serve_forever()


if __name__ == "__main__":
    main()
