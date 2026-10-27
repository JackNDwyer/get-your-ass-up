#!/usr/bin/env python3
"""Claude Code hooks → the coach. Usage: hook.py prompt|tool|tool-failed|stop|notify

Reads the hook's JSON from stdin and tells the coach what happened. Standard library only, and
fail-open: if the coach isn't running or anything goes wrong, Claude carries on untouched. The one
exception is the optional prompt gate (GYAU_GATE=1 on the coach), which exits 2 to block a prompt
while you owe reps.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

COACH = f"http://127.0.0.1:{os.environ.get('GYAU_PORT', '7777')}"
TIMEOUT = 1.0
MAX_PROMPT_CHARS = 500
TAB_MARKER = "#tab"

# Test and build runs worth reacting to. Everything else Claude runs (ls, git, grep) is ignored.
# Matched at the start of each piece of a compound command, after wrappers like `uv run`.
TEST_COMMAND = re.compile(
    r"(?:pytest|py\.test|python3? -m (?:pytest|unittest)|tox|nox|"
    r"jest|vitest|mocha|bun test|deno test|"
    r"(?:npm|pnpm|yarn|bun)(?: run)? (?:test|build|typecheck)|"
    r"tsc|cargo (?:test|build|check|clippy)|go (?:test|build|vet)|"
    r"make(?: (?:test|check|build|all))?|ctest|"
    r"mvn .*(?:test|verify|package)|(?:\./gradlew|gradle) .*(?:test|build)|"
    r"swift (?:test|build)|dotnet (?:test|build)|rspec|phpunit|mix test)"
    r"(?:$|\s)"
)
WRAPPERS = re.compile(r"^(?:\w+=\S*\s+)*(?:(?:uv run|poetry run|pipenv run|npx|pnpm exec|bunx|time)\s+)*")


def looks_like_test(command: str) -> bool:
    for piece in re.split(r"&&|\|\||[;|()]", command):
        piece = WRAPPERS.sub("", piece.strip())
        if TEST_COMMAND.match(piece):
            return True
    return False


def call(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        COACH + path,
        data=data,
        method="POST" if path != "/status" else "GET",
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read() or b"{}")


def on_prompt(event: dict) -> int:
    prompt = str(event.get("prompt", ""))
    try:
        status = call("/status")
    except Exception:
        return 0  # coach not running: never block Claude
    if status.get("block"):
        if TAB_MARKER in prompt.lower():
            call("/tab-it", {})
        else:
            print(
                f"Coach says: {status.get('current', 'your workout')} first. Tell the coach when "
                f"you're done, or put {TAB_MARKER} in your prompt to put it on your tab (with interest).",
                file=sys.stderr,
            )
            return 2
    project = Path(event.get("cwd") or ".").name
    call("/work-started", {"prompt": prompt[:MAX_PROMPT_CHARS], "project": project})
    return 0


def on_tool(event: dict, failed: bool) -> int:
    if event.get("tool_name") != "Bash" or event.get("is_interrupt"):
        return 0
    command = str((event.get("tool_input") or {}).get("command", ""))
    if looks_like_test(command):
        call("/tool-result", {"command": command[:200], "passed": not failed})
    return 0


def main(kind: str) -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    try:
        if kind == "prompt":
            return on_prompt(event)
        if kind in ("tool", "tool-failed"):
            return on_tool(event, failed=kind == "tool-failed")
        if kind == "stop":
            call("/work-finished", {})
        elif kind == "notify":
            call("/needs-you", {})
    except Exception:
        pass  # fail open
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
