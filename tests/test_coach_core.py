import asyncio
import json
from datetime import date

import pytest
from aiohttp.test_utils import TestClient, TestServer

from coach_core import Coach, WorkoutLog, build_http_app, number_words, reps_for
from plugin.scripts.workout_stats import summary_line, today_totals

# Real timers, just short ones.
DELAY = 0.05
REPEAT = 0.1


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def log(tmp_path):
    return WorkoutLog(tmp_path / "log.jsonl")


@pytest.fixture
def said():
    return []


@pytest.fixture
def coach(log, said):
    return Coach(said.append, log=log, first_delay=DELAY, repeat_every=REPEAT)


def events(log):
    return [json.loads(line) for line in log.path.read_text().splitlines()] if log.path.exists() else []


async def test_finish_before_timer_fires_is_silent(coach, said):
    coach.work_started()
    await asyncio.sleep(DELAY / 2)
    coach.work_finished()
    await asyncio.sleep(DELAY * 2)
    assert said == []
    assert coach.current is None


async def test_timer_fires_and_assigns_workout(coach, said, log):
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    assert said == ["Get your ass up. Ten pushups!"]
    assert coach.current.exercise == "pushups"
    last = events(log)[-1]
    assert (last["type"], last["exercise"], last["count"]) == ("assigned", "pushups", 10)


async def test_finish_after_timer_says_sit(coach, said):
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    coach.work_finished()
    assert said[-1] == "Agent's done. You may sit."
    # A second Stop (e.g. after a follow-up with no new workout) stays quiet.
    coach.work_finished()
    assert said.count("Agent's done. You may sit.") == 1


async def test_keeps_assigning_and_rotates_while_working(coach, said):
    coach.work_started()
    await asyncio.sleep(DELAY + REPEAT * 1.5)
    coach.work_finished()
    assert said[0].startswith("Get your ass up.")
    assert said[1].startswith("Claude's still working. So are you.")
    assert "squats" in said[1]


async def test_repeated_work_started_does_not_restart_timer(coach, said):
    coach.work_started()
    await asyncio.sleep(DELAY * 0.7)
    coach.work_started()  # e.g. a queued prompt while Claude is still busy
    await asyncio.sleep(DELAY * 0.7)
    assert len(said) == 1  # fired on the original schedule, not pushed back
    coach.work_finished()


async def test_needs_you_cancels_timer(coach, said):
    coach.work_started()
    coach.needs_you()
    await asyncio.sleep(DELAY * 2)
    assert said == ["Claude needs you. Back to the desk."]
    assert not coach.working


async def test_injury_stops_everything(log, said):
    coach = Coach(said.append, log=log, first_delay=DELAY)
    coach.work_started()
    verdict = coach.judge_excuse("I think I hurt my wrist")
    assert verdict["verdict"] == "injury"
    await asyncio.sleep(DELAY * 2)
    assert said == []

    # Still resting: new work doesn't start new workouts.
    coach.work_started()
    await asyncio.sleep(DELAY * 2)
    assert said == []

    coach.resume()
    coach.work_finished()
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    assert len(said) == 1
    coach.work_finished()


def test_reps_scale_with_working_time():
    assert reps_for("pushups", 20) == 10
    assert reps_for("squats", 120) == 30
    assert reps_for("plank", 60) == 45
    assert reps_for("pushups", 3600) == 30  # capped at 3x


async def test_reps_use_time_since_work_started(log, said):
    clock = FakeClock()
    coach = Coach(said.append, log=log, first_delay=DELAY, clock=clock)
    coach.work_started()
    clock.now += 240  # Claude has been grinding for four minutes
    await asyncio.sleep(DELAY * 1.5)
    assert said == ["Get your ass up. Thirty pushups!"]
    coach.work_finished()


def test_excuses(coach):
    assert coach.judge_excuse("sorry, I'm on a call")["verdict"] == "legit"

    coach.current = None
    weak = coach.judge_excuse("I'm tired")
    assert weak["verdict"] == "weak"
    assert weak["penalty"] == "5 pushups"

    from coach_core import Workout

    coach.current = Workout("plank", 30)
    assert coach.judge_excuse("later")["now_owed"] == "45 seconds of plank"


def test_log_reps_and_stats(coach, log):
    coach.log_reps("Push-ups", 100)
    coach.log_reps("pushup", 40)
    coach.log_reps("plank", 180)
    coach.judge_excuse("I'm tired")
    totals = today_totals(log.path)
    assert totals["reps"] == {"pushups": 140, "plank": 180}
    assert totals["weak_excuses"] == 1
    assert summary_line(totals) == "Today: 140 pushups · 3 min of plank"


def test_stats_only_count_today_and_skip_junk(log):
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text(
        '{"ts": "2020-01-01T09:00:00+00:00", "type": "reps", "exercise": "squats", "count": 99}\n'
        "not json\n"
    )
    log.append("reps", exercise="squats", count=15)
    assert today_totals(log.path, date.today())["reps"] == {"squats": 15}


def test_summary_line_formats():
    assert summary_line({"reps": {}}) == "Today: nothing. Get your ass up."
    assert summary_line({"reps": {"plank": 45, "burpees": 10, "squats": 30}}) == (
        "Today: 30 squats · 45 s of plank · 10 burpees"
    )
    assert summary_line({"reps": {"plank": 90}}) == "Today: 1.5 min of plank"


def test_number_words():
    assert number_words(15) == "fifteen"
    assert number_words(45) == "forty-five"
    assert number_words(30) == "thirty"


async def test_http_endpoints(coach, said):
    async with TestClient(TestServer(build_http_app(coach))) as client:
        assert (await client.post("/work-started")).status == 200
        await asyncio.sleep(DELAY * 1.5)
        status = await (await client.get("/status")).json()
        assert status == {"working": True, "resting": False, "current": "10 pushups"}

        assert (await client.post("/work-finished")).status == 200
        assert (await client.post("/needs-you")).status == 200
        assert said == [
            "Get your ass up. Ten pushups!",
            "Agent's done. You may sit.",
            "Claude needs you. Back to the desk.",
        ]
