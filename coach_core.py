"""Everything the coach does that isn't audio: timers, workouts, excuses, the rep log.

Kept free of LiveKit so it can be unit-tested without a mic, a speaker or a network.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aiohttp import web

from plugin.scripts.workout_stats import EXERCISES, LOG_PATH, normalize_exercise

# Base reps for a fresh workout. Plank is in seconds.
BASE_REPS = {"pushups": 10, "squats": 15, "plank": 30, "lunges": 10}

# Reps grow with how long Claude has been grinding: +100% every 2 minutes, capped at 3x.
SCALE_PER_SECOND = 1 / 120
MAX_SCALE = 3.0

PENALTY_REPS = 5
PENALTY_PLANK_SECONDS = 15

INJURY_WORDS = re.compile(
    r"\b(hurt|hurts|injur\w*|pain|painful|sprain\w*|broken|pulled a muscle|strain\w*|"
    r"bleeding|dizzy|faint\w*|can'?t breathe|torn)\b",
    re.IGNORECASE,
)
LEGIT_WORDS = re.compile(
    r"\b(on a call|meeting|zoom|presenting|interview|driving|on a plane|on a train|"
    r"holding (?:the|a|my) baby|feeding (?:the|my) baby)\b",
    re.IGNORECASE,
)

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    """15 -> 'fifteen'. TTS reads words more reliably than digits, and they look better on screen."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    return str(n)


@dataclass
class Workout:
    exercise: str
    count: int

    def spoken(self) -> str:
        n = number_words(self.count).capitalize()
        if self.exercise == "plank":
            return f"Plank. {n} seconds."
        if self.exercise == "lunges":
            return f"{n} lunges. Each leg."
        return f"{n} {self.exercise}!"

    def describe(self) -> str:
        unit = "seconds of plank" if self.exercise == "plank" else self.exercise
        return f"{self.count} {unit}"


def reps_for(exercise: str, working_seconds: float) -> int:
    scale = min(1 + working_seconds * SCALE_PER_SECOND, MAX_SCALE)
    return max(5, round(BASE_REPS[exercise] * scale / 5) * 5)


class WorkoutLog:
    """Append-only JSONL log at ~/.get-your-ass-up/log.jsonl."""

    def __init__(self, path: Path = LOG_PATH):
        self.path = path

    def append(self, type_: str, **fields) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "type": type_, **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")


class Coach:
    """Turns Claude Code's work events into yelling.

    `speak` is called with a line to say out loud; in the real app it is `session.say`.
    """

    def __init__(
        self,
        speak: Callable[[str], object],
        *,
        log: WorkoutLog | None = None,
        first_delay: float = 20.0,
        repeat_every: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._speak = speak
        self.log = log or WorkoutLog()
        self.first_delay = first_delay
        self.repeat_every = repeat_every
        self._clock = clock

        self.working = False
        self.resting = False  # set when the user says they're hurt; no more workouts
        self.current: Workout | None = None  # last workout assigned in this stretch of work
        self._work_started_at = 0.0
        self._next_exercise = 0
        self._timer: asyncio.Task | None = None

    # -- Claude Code events --------------------------------------------------------------

    def work_started(self) -> None:
        if not self.working:
            self.working = True
            self._work_started_at = self._clock()
            self.current = None
        if self._timer is None and not self.resting:
            self._timer = asyncio.create_task(self._drill())

    def work_finished(self) -> None:
        self._cancel_timer()
        was_working, self.working = self.working, False
        if was_working and self.current is not None:
            self.current = None
            self._speak("Agent's done. You may sit.")

    def needs_you(self) -> None:
        self._cancel_timer()
        self.working = False
        self.current = None
        self._speak("Claude needs you. Back to the desk.")

    # -- talking back --------------------------------------------------------------------

    def log_reps(self, exercise: str, count: int) -> dict:
        exercise = normalize_exercise(exercise)
        self.log.append("reps", exercise=exercise, count=int(count))
        return {"logged": f"{count} {exercise}"}

    def judge_excuse(self, excuse: str) -> dict:
        if INJURY_WORDS.search(excuse):
            self.rest()
            verdict = {"verdict": "injury", "say": "Stop. No more workouts. Rest, and see someone if it's bad."}
        elif LEGIT_WORDS.search(excuse):
            self.current = None
            verdict = {"verdict": "legit", "say": "Fine. You get a pass. This once."}
        else:
            penalty = self._add_penalty()
            verdict = {"verdict": "weak", "penalty": penalty.describe(), "now_owed": self.current.describe()}
        self.log.append("excuse", excuse=excuse, verdict=verdict["verdict"])
        return verdict

    def rest(self) -> None:
        """The user is hurt. No more workouts until they say they're fine.

        Deliberately doesn't interrupt speech: this runs inside tool calls, and cutting the
        session off there would also kill the "stop and rest" reply that follows.
        """
        self.resting = True
        self.current = None
        self._cancel_timer()

    def resume(self) -> None:
        self.resting = False

    # -- internals -----------------------------------------------------------------------

    async def _drill(self) -> None:
        try:
            await asyncio.sleep(self.first_delay)
            while self.working and not self.resting:
                self._assign(repeat=self.current is not None)
                await asyncio.sleep(self.repeat_every)
        finally:
            if self._timer is asyncio.current_task():
                self._timer = None

    def _assign(self, *, repeat: bool) -> Workout:
        exercise = EXERCISES[self._next_exercise % len(EXERCISES)]
        self._next_exercise += 1
        workout = Workout(exercise, reps_for(exercise, self._clock() - self._work_started_at))
        self.current = workout
        self.log.append("assigned", exercise=workout.exercise, count=workout.count)
        opener = "Claude's still working. So are you." if repeat else "Get your ass up."
        self._speak(f"{opener} {workout.spoken()}")
        return workout

    def _add_penalty(self) -> Workout:
        if self.current is None:
            exercise = EXERCISES[self._next_exercise % len(EXERCISES)]
            self.current = Workout(exercise, 0)
        extra = PENALTY_PLANK_SECONDS if self.current.exercise == "plank" else PENALTY_REPS
        self.current = Workout(self.current.exercise, self.current.count + extra)
        return Workout(self.current.exercise, extra)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


# -- local HTTP server the Claude Code hooks curl ------------------------------------------

DEFAULT_PORT = int(os.environ.get("GYAU_PORT", "7777"))


def build_http_app(coach: Coach) -> web.Application:
    def handler(event: Callable[[], None]):
        async def handle(_: web.Request) -> web.Response:
            event()
            return web.json_response({"ok": True})

        return handle

    async def status(_: web.Request) -> web.Response:
        current = coach.current.describe() if coach.current else None
        return web.json_response({"working": coach.working, "resting": coach.resting, "current": current})

    app = web.Application()
    app.add_routes(
        [
            web.post("/work-started", handler(coach.work_started)),
            web.post("/work-finished", handler(coach.work_finished)),
            web.post("/needs-you", handler(coach.needs_you)),
            web.get("/status", status),
        ]
    )
    return app


async def start_http_server(coach: Coach, port: int = DEFAULT_PORT) -> web.AppRunner:
    runner = web.AppRunner(build_http_app(coach), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
