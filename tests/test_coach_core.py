import asyncio
import json
from datetime import date

import pytest
from aiohttp.test_utils import TestClient, TestServer

from coach_core import DONE_LINES, NEEDS_YOU_LINE, Coach, WorkoutLog, build_http_app, number_words, reps_for
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
    assert said[-1] == DONE_LINES[0]
    # A second Stop (e.g. after a follow-up with no new workout) stays quiet.
    coach.work_finished()
    assert sum(line in DONE_LINES for line in said) == 1


async def test_done_lines_rotate(coach, said):
    for _ in range(2):
        coach.work_started()
        await asyncio.sleep(DELAY * 1.5)
        coach.work_finished()
    assert [line for line in said if line in DONE_LINES] == list(DONE_LINES[:2])


async def test_reaction_waits_a_beat(log, said):
    coach = Coach(said.append, log=log, first_delay=DELAY, react_delay=DELAY)
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    coach.work_finished()
    assert said[-1] != DONE_LINES[0]  # not yet
    await asyncio.sleep(DELAY * 1.5)
    assert said[-1] == DONE_LINES[0]


async def test_new_work_cancels_pending_reaction(log, said):
    coach = Coach(said.append, log=log, first_delay=DELAY, react_delay=DELAY)
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    coach.work_finished()
    coach.work_started()  # Claude picked straight back up
    await asyncio.sleep(DELAY * 0.8)
    coach.work_finished()
    await asyncio.sleep(DELAY * 2)
    assert DONE_LINES[0] not in said


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
    assert said == [NEEDS_YOU_LINE]
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
        assert {k: status[k] for k in ("working", "resting", "current", "block")} == {
            "working": True,
            "resting": False,
            "current": "10 pushups",
            "block": False,
        }

        assert (await client.post("/work-finished")).status == 200
        assert (await client.post("/needs-you")).status == 200
        assert said == [
            "Get your ass up. Ten pushups!",
            DONE_LINES[0],
            NEEDS_YOU_LINE,
        ]


async def test_camera_nags_and_grows_an_unpaid_workout(log, said):
    coach = Coach(said.append, log=log, first_delay=DELAY, repeat_every=10, nag_every=REPEAT)
    coach.saw("desk")
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    assert said == ["Get your ass up. Ten pushups!"]
    await asyncio.sleep(REPEAT * 1.6)
    assert said[-1].startswith("Still not moving. Five more pushups.")
    assert coach.current.count == 15
    coach.work_finished()


async def test_camera_does_not_nag_while_you_exercise(log, said):
    coach = Coach(said.append, log=log, first_delay=DELAY, repeat_every=10, nag_every=REPEAT)
    coach.saw("desk")
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    for _ in range(4):
        coach.saw("exercising")
        await asyncio.sleep(REPEAT / 2)
    assert len(said) == 1
    coach.work_finished()
