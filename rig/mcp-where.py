"""A tiny MCP server for the rig: one tool that says where it runs.

It speaks MCP over its own input and output, one JSON message a line,
with nothing but the standard library. The rig starts it on a target
to prove that an MCP server added with `hermes onsite mcp` runs there.
"""

from __future__ import annotations

import json
import os
import sys


SERVER_NAME = "hands-rig-where"
SERVER_VERSION = "1"
FALLBACK_PROTOCOL = "2025-06-18"

TOOL = {
    "name": "where",
    "description": "Say which machine and folder this server runs in.",
    "inputSchema": {"type": "object", "properties": {}},
}


def describe_place() -> str:
    folders = os.environ.get("PATH", "").split(":")

    return "\n".join([
        f"node: {os.uname().nodename}",
        f"cwd: {os.getcwd()}",
        f"path_folders: {len(folders)}",
    ])


def answer(method: str, params: dict):
    if method == "initialize":
        return {
            "protocolVersion": params.get(
                "protocolVersion", FALLBACK_PROTOCOL,
            ),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    if method == "tools/list":
        return {"tools": [TOOL]}

    if method == "tools/call":
        return {"content": [{"type": "text", "text": describe_place()}]}

    if method == "ping":
        return {}

    return None


def reply(message_id, result) -> None:
    body = {"jsonrpc": "2.0", "id": message_id}

    if result is None:
        body["error"] = {"code": -32601, "message": "unknown method"}
    else:
        body["result"] = result

    sys.stdout.write(json.dumps(body) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue

        message = json.loads(line)

        # A message with no id is a notice. It gets no reply.
        if "id" not in message:
            continue

        reply(
            message["id"],
            answer(message.get("method", ""), message.get("params") or {}),
        )


if __name__ == "__main__":
    main()
