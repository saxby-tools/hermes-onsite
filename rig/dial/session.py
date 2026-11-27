"""One Hermes session for the dial-in walk, driven a line at a time.

It runs inside the server container. The walk has to act between
two tool calls of the same session: cut the link, freeze the laptop,
bring it back. So the session stays open, and each line it reads is
one prompt. What the prompt did is written back as one line of JSON.

Standard library only.
"""

from __future__ import annotations

import json
import sys
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acp  # noqa: E402  (the path line above has to come first)


HERMES = "/opt/hermes/.venv/bin/hermes"
LOG_FILE = Path("/opt/data/logs/dial-session.log")


def say(answer: dict) -> None:
    print(json.dumps(answer), flush=True)


def describe_turn(turn: acp.Turn, seconds: float) -> dict:
    tools = [
        {
            "title": tool.title,
            "status": tool.status,
            "seconds": round(tool.seconds, 2),
            "text": tool.text,
        }
        for tool in turn.tools
    ]

    return {
        "seconds": round(seconds, 2),
        "tools": tools,
        "message": turn.message,
    }


def run_prompt(client: acp.AcpClient, prompt: str) -> dict:
    started = time.monotonic()

    try:
        turn = client.prompt(prompt)
    except acp.AcpError as error:
        return {"error": str(error)}

    return describe_turn(turn, time.monotonic() - started)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: session.py FOLDER", file=sys.stderr)
        return 2

    client = acp.AcpClient([HERMES, "acp"], Path(sys.argv[1]), LOG_FILE)

    try:
        client.start()
    except acp.AcpError as error:
        say({"error": str(error)})
        return 1

    say({"ready": True})

    for line in sys.stdin:
        prompt = line.strip()

        if not prompt:
            continue

        say(run_prompt(client, prompt))

    client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
