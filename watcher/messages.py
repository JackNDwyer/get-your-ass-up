"""What the watcher tells you when something's wrong. Standard library only, so it's easy to test."""

from __future__ import annotations

import json
import urllib.error

NO_CAMERA = (
    "Couldn't open any camera. On a Mac that almost always means your terminal app doesn't have camera "
    "access: System Settings → Privacy & Security → Camera, turn on the terminal you run this from "
    "(Terminal, iTerm, Ghostty, VS Code…), then quit it completely (⌘Q) and reopen it. If macOS just "
    "asked for permission, click Allow and run this again."
)


def camera_problem(requested: int, working: list[int]) -> str:
    """Why the camera didn't open: no permission at all, or just the wrong index."""
    if not working:
        return NO_CAMERA
    listed = ", ".join(str(i) for i in working)
    return (
        f"Camera {requested} didn't open. These ones do: {listed}. "
        f"Try --camera {working[0]}, or run with --list-cameras to see them all."
    )


def link_problem(error: BaseException, coach_url: str) -> str:
    """One line on why the watcher can't get through to the coach."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 503:
            return (
                "The coach's vision model is off (GYAU_VISION=0). Run the watcher with --no-cloud to "
                "label frames on your Mac instead."
            )
        try:
            detail = json.loads(error.read() or b"{}").get("error") or ""
        except (ValueError, OSError):
            detail = ""
        return f"The vision model couldn't label a frame ({detail or f'HTTP {error.code}'}). Still trying."
    reason = getattr(error, "reason", error)
    if isinstance(reason, ConnectionRefusedError):
        return (
            f"Can't reach the coach at {coach_url}. Start it with `lk agent console coach.py` "
            "(if you changed GYAU_PORT, use the same value here). Still trying."
        )
    return f"Trouble talking to the coach: {error}. Still trying."
