#!/usr/bin/env python3
"""Write two weeks of realistic fake reps to a separate demo log, for filming the chart demo.

    uv run scripts/seed_demo_log.py            # writes ~/.get-your-ass-up/demo-log.jsonl
    GYAU_LOG=~/.get-your-ass-up/demo-log.jsonl lk agent console coach.py

Never touches the real log. This week is deliberately worse than last week, so there's
something to roast.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

DEMO_LOG = Path.home() / ".get-your-ass-up" / "demo-log.jsonl"
SETS = {"pushups": (10, 25), "squats": (15, 30), "plank": (30, 60), "lunges": (10, 20)}


def demo_events(today: date, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    events = []
    for days_ago in range(13, -1, -1):
        day = today - timedelta(days=days_ago)
        this_week = days_ago < 7
        weekend = day.weekday() >= 5
        sessions = rng.randint(1, 3) if weekend else rng.randint(4, 9) - (1 if this_week else 0)
        minute = 9 * 60
        for _ in range(max(sessions, 0)):
            minute += rng.randint(20, 70)
            ts = datetime.combine(day, time(min(minute // 60, 23), minute % 60)).astimezone()
            exercise = rng.choice(list(SETS))
            low, high = SETS[exercise]
            count = rng.randint(low, high) // 5 * 5
            stamp = ts.isoformat(timespec="seconds")
            events.append({"ts": stamp, "type": "assigned", "exercise": exercise, "count": count})
            if rng.random() < (0.75 if this_week else 0.9):
                events.append({"ts": stamp, "type": "reps", "exercise": exercise, "count": count})
            elif rng.random() < 0.6:
                events.append({"ts": stamp, "type": "excuse", "excuse": "I'm tired", "verdict": "weak"})
    return events


def main(path: Path = DEMO_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    events = demo_events(date.today())
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    print(f"Wrote {len(events)} fake events to {path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEMO_LOG)
