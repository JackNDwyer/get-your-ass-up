#!/usr/bin/env python3
"""Print today's workout totals as one line, e.g. `Today: 140 pushups · 3 min of plank`.

Standard library only: the Claude Code plugin runs this with whatever python3 is on the PATH.
The coach imports the reading/formatting helpers from here so there's one source of truth.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

LOG_PATH = Path(os.environ.get("GYAU_LOG", Path.home() / ".get-your-ass-up" / "log.jsonl"))

EXERCISES = ["pushups", "squats", "plank", "lunges"]

_ALIASES = {
    "pushup": "pushups",
    "push-up": "pushups",
    "push-ups": "pushups",
    "push up": "pushups",
    "push ups": "pushups",
    "squat": "squats",
    "planks": "plank",
    "lunge": "lunges",
}


def normalize_exercise(name: str) -> str:
    name = name.strip().lower()
    return _ALIASES.get(name, name)


def read_events(path: Path = LOG_PATH, day: date | None = None) -> list[dict]:
    day = day or date.today()
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
            if datetime.fromisoformat(event["ts"]).date() == day:
                events.append(event)
        except (ValueError, KeyError):
            continue  # a half-written or hand-edited line shouldn't kill the stats
    return events


def tab_balance(path: Path = LOG_PATH) -> dict[str, int]:
    """Reps put on the tab and not yet paid off, all time (a tab doesn't reset at midnight)."""
    if not path.exists():
        return {}
    tab: Counter[str] = Counter()
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
            if event["type"] == "tab":
                tab[event["exercise"]] += int(event["count"])
        except (ValueError, KeyError):
            continue
    return {exercise: n for exercise, n in tab.items() if n > 0}


def today_totals(path: Path = LOG_PATH, day: date | None = None) -> dict:
    events = read_events(path, day)
    reps: Counter[str] = Counter()
    for e in events:
        if e["type"] == "reps":
            reps[e["exercise"]] += int(e["count"])
    return {
        "reps": dict(reps),
        "workouts_assigned": sum(e["type"] == "assigned" for e in events),
        "excuses": sum(e["type"] == "excuse" for e in events),
        "weak_excuses": sum(e["type"] == "excuse" and e.get("verdict") == "weak" for e in events),
        "tab": tab_balance(path),
    }


def _plank(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s of plank"
    minutes = round(seconds / 60, 1)
    return f"{minutes:g} min of plank"


def _amounts(counts: dict[str, int]) -> list[str]:
    order = EXERCISES + sorted(set(counts) - set(EXERCISES))
    return [_plank(counts[e]) if e == "plank" else f"{counts[e]} {e}" for e in order if counts.get(e)]


def summary_line(totals: dict) -> str:
    reps, tab = totals["reps"], totals.get("tab") or {}
    line = "Today: " + " · ".join(_amounts(reps)) if reps else "Today: nothing. Get your ass up."
    if tab:
        line += " · On the tab: " + ", ".join(_amounts(tab))
    return line


if __name__ == "__main__":
    print(summary_line(today_totals()))
    sys.exit(0)
