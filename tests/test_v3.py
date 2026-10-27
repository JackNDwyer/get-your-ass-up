"""Version 3: knows the task, reacts to tests, haggles, keeps a tab, and believes the webcam."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from coach_core import (
    DONE_LINES,
    PASS_FALLBACK,
    Coach,
    Workout,
    WorkoutLog,
    build_http_app,
)
from plugin.scripts.workout_stats import summary_line, tab_balance, today_totals

DELAY = 0.05
REPEAT = 0.1
HOOK = Path(__file__).parent.parent / "plugin" / "scripts" / "hook.py"


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
def jabs():
    return []


@pytest.fixture
def notes():
    return []


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def coach(log, said, jabs, notes, clock):
    return Coach(
        said.append, log=log, first_delay=DELAY, repeat_every=REPEAT,
        jab=jabs.append, on_context=notes.append, clock=clock,
    )  # fmt: skip


def owe(coach, exercise="squats", count=15):
    coach._set_current(Workout(exercise, count))


# -- knows what you're building ------------------------------------------------------------


async def test_prompt_becomes_context_and_workouts_get_a_jab(coach, said, jabs, notes):
    coach.work_started(prompt="fix the login bug again", project="dating-crm")
    assert "fix the login bug again" in notes[-1] and "dating-crm" in notes[-1]
    await asyncio.sleep(DELAY * 1.5)
    assert said == ["Get your ass up. Ten pushups!"]  # the order itself stays exact
    assert "fix the login bug again" in jabs[-1]
    coach.work_finished()


async def test_no_prompt_still_works(coach, said, jabs):
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    assert said == ["Get your ass up. Ten pushups!"]
    assert jabs == []  # nothing to joke about yet
    coach.work_finished()


# -- knows if the code works ---------------------------------------------------------------


async def test_failing_tests_start_the_workout_early(coach, said, jabs):
    coach.work_started()
    coach.test_result("pytest -q", passed=False)
    assert said == ["Tests failed. Get your ass up. Ten pushups!"]
    assert "failed" in jabs[-1]
    assert coach.current == Workout("pushups", 10)
    coach.work_finished()


async def test_failing_tests_add_to_what_you_owe(coach, said):
    coach.working = True
    owe(coach, "squats", 15)
    coach.test_result("npm test", passed=False)
    assert said == ["Tests failed. That's five more squats."]
    assert coach.current == Workout("squats", 20)


async def test_passing_tests_get_a_grudging_line_and_a_cooldown(log, said, clock):
    coach = Coach(said.append, log=log, clock=clock)  # no LLM: falls back to a fixed line
    coach.test_result("pytest", passed=True)
    coach.test_result("pytest", passed=True)  # within the cooldown
    assert said == [PASS_FALLBACK]
    clock.now += 60
    coach.test_result("pytest", passed=True)
    assert said == [PASS_FALLBACK, PASS_FALLBACK]


# -- bargaining and the tab ----------------------------------------------------------------


def test_swap_never_lowers_effort(coach):
    owe(coach, "pushups", 10)
    result = coach.bargain("swap", "squats")
    assert result["now_owed"] == "17 squats"  # 10 pushups = 15 squats, plus the 10% fee
    owe(coach, "squats", 15)
    assert coach.bargain("swap", "plank")["now_owed"] == "33 seconds of plank"
    assert "error" in coach.bargain("swap", "jumping jacks")
    assert "error" in coach.bargain("swap", "plank")


def test_tab_charges_interest_and_gets_paid_down(coach, log):
    owe(coach, "squats", 15)
    result = coach.bargain("tab")
    assert result["put_on_tab"] == "23 squats"
    assert coach.current is None
    assert tab_balance(log.path) == {"squats": 23}
    assert "On the tab: 23 squats" in summary_line(today_totals(log.path))

    paid = coach.log_reps("squats", 10)
    assert paid["paid_off_tab"] == "10 squats"
    assert tab_balance(log.path) == {"squats": 13}
    coach.log_reps("squats", 50)  # overpaying doesn't make the tab negative
    assert tab_balance(log.path) == {}


def test_reps_pay_the_current_workout_first(coach, log):
    coach.log.append("tab", exercise="squats", count=10)
    owe(coach, "squats", 15)
    result = coach.log_reps("squats", 20)
    assert coach.current is None
    assert result["paid_off_tab"] == "5 squats"
    assert tab_balance(log.path) == {"squats": 5}


def test_nothing_to_bargain_with(coach):
    assert "error" in coach.bargain("tab")


# -- the webcam ----------------------------------------------------------------------------


async def test_phone_while_owing_costs_reps(coach, said, clock):
    coach.working = True
    owe(coach, "pushups", 10)
    coach.saw("desk")
    coach.saw("phone")
    assert said == ["Phone down. Five more pushups."]
    assert coach.current == Workout("pushups", 15)
    coach.saw("desk")
    coach.saw("phone")  # cooldown: no double penalty a few seconds later
    assert len(said) == 1
    clock.now += 60
    coach.saw("desk")
    coach.saw("phone")
    assert len(said) == 2


async def test_phone_while_claude_works_starts_a_workout(coach, said):
    coach.work_started()
    coach.saw("phone")
    assert said == ["I see that phone. Get your ass up. Ten pushups!"]
    coach.work_finished()


def test_phone_when_idle_is_fine(coach, said):
    coach.saw("phone")
    assert said == []


def test_claiming_done_from_the_desk_is_a_lie(coach, log):
    coach.saw("desk")
    owe(coach, "squats", 15)
    coach.saw("desk")
    result = coach.log_reps("squats", 15)
    assert result["lie"] is True
    assert coach.current == Workout("squats", 15)
    assert today_totals(log.path)["reps"] == {}


def test_claiming_done_after_leaving_the_desk_is_believed(coach, log):
    coach.saw("desk")
    owe(coach, "squats", 15)
    coach.saw("exercising")
    coach.saw("desk")
    assert "lie" not in coach.log_reps("squats", 15)
    assert coach.current is None


def test_no_watcher_means_no_lie_detector(coach):
    owe(coach, "squats", 15)
    assert "lie" not in coach.log_reps("squats", 15)


def test_camera_count_beats_exaggeration(coach, log):
    coach.saw("desk")
    owe(coach, "pushups", 20)
    coach.saw("exercising")
    coach.camera_reps("pushups", 8)
    result = coach.log_reps("pushups", 20)
    assert result["lie"] is True
    assert today_totals(log.path)["reps"] == {"pushups": 8}
    assert coach.current == Workout("pushups", 12)


def test_camera_finishing_the_workout_logs_it(coach, said, log):
    owe(coach, "squats", 15)
    coach.camera_reps("squats", 14)
    assert coach.current is not None
    coach.camera_reps("squats", 15)
    assert coach.current is None
    assert said == ["Fifteen. I counted. Sit down."]
    assert json.loads(log.path.read_text().splitlines()[-1])["source"] == "camera"


def test_webcam_never_overrides_rest(coach, said):
    coach.working = True
    owe(coach, "pushups", 10)
    coach.judge_excuse("I hurt my wrist")
    assert coach.resting
    coach.saw("phone")
    coach.camera_reps("pushups", 10)
    coach.test_result("pytest", passed=False)
    assert said == []
    assert coach.current is None


# -- gate mode -----------------------------------------------------------------------------


async def test_gate_keeps_reps_owed_after_claude_finishes(log, said):
    coach = Coach(said.append, log=log, first_delay=DELAY, gate=True)
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    coach.work_finished()
    assert said[-1] == "Claude's done. You still owe me 10 pushups. No more prompts till you do them."
    assert coach.status()["block"] is True
    coach.log_reps("pushups", 10)
    assert coach.status()["block"] is False


async def test_without_gate_claude_finishing_forgives(coach, said):
    coach.work_started()
    await asyncio.sleep(DELAY * 1.5)
    coach.work_finished()
    assert said[-1] == DONE_LINES[0]
    assert coach.status()["block"] is False


# -- HTTP and the real hook script ---------------------------------------------------------


async def test_http_v3_endpoints(log, said):
    coach = Coach(said.append, log=log, first_delay=60, repeat_every=60)

    async def classify(jpeg: bytes) -> str:
        return "phone" if jpeg == b"phone" else "desk"

    async with TestClient(TestServer(build_http_app(coach, classify))) as client:
        await client.post("/work-started", json={"prompt": "add dark mode", "project": "app"})
        assert coach.task == {"prompt": "add dark mode", "project": "app"}
        resp = await (await client.post("/frame", data=b"phone")).json()
        assert resp["label"] == "phone"
        assert said[-1].startswith("I see that phone.")
        await client.post("/tool-result", json={"command": "pytest", "passed": False})
        assert coach.current.count == 15
        status = await (await client.post("/reps-seen", json={"exercise": "pushups", "count": 3})).json()
        assert status["camera_count"] == 3
        tab = await (await client.post("/tab-it")).json()
        assert tab["deal"] == "tab"
        await client.post("/work-finished")


async def test_frame_without_vision_is_503(coach):
    async with TestClient(TestServer(build_http_app(coach))) as client:
        assert (await client.post("/frame", data=b"x")).status == 503


def run_hook(kind: str, event: dict, port: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK), kind],
        input=json.dumps(event), capture_output=True, text=True, timeout=10,
        env={**os.environ, "GYAU_PORT": str(port)},
    )  # fmt: skip


async def test_hook_fails_open_without_a_coach(unused_tcp_port):
    result = await asyncio.to_thread(run_hook, "prompt", {"prompt": "hi", "cwd": "/x/proj"}, unused_tcp_port)
    assert result.returncode == 0
    result = await asyncio.to_thread(run_hook, "tool-failed", {"tool_name": "Bash"}, unused_tcp_port)
    assert result.returncode == 0


async def test_hook_end_to_end(log, said, unused_tcp_port):
    coach = Coach(said.append, log=log, first_delay=60, gate=True)
    server = TestServer(build_http_app(coach), port=unused_tcp_port)
    await server.start_server()
    try:
        ok = await asyncio.to_thread(run_hook, "prompt", {"prompt": "build the thing", "cwd": "/a/tv-app"}, unused_tcp_port)
        assert ok.returncode == 0
        assert coach.task == {"prompt": "build the thing", "project": "tv-app"}

        test_run = {"tool_name": "Bash", "tool_input": {"command": "uv run pytest -q"}, "error": "Exit code 1"}
        await asyncio.to_thread(run_hook, "tool-failed", test_run, unused_tcp_port)
        assert said[-1] == "Tests failed. Get your ass up. Ten pushups!"
        ls = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        await asyncio.to_thread(run_hook, "tool", ls, unused_tcp_port)  # not a test: ignored
        assert len(said) == 1

        await asyncio.to_thread(run_hook, "stop", {}, unused_tcp_port)
        blocked = await asyncio.to_thread(run_hook, "prompt", {"prompt": "next thing"}, unused_tcp_port)
        assert blocked.returncode == 2
        assert "10 pushups first" in blocked.stderr

        bypass = await asyncio.to_thread(run_hook, "prompt", {"prompt": "next thing #tab"}, unused_tcp_port)
        assert bypass.returncode == 0
        assert tab_balance(log.path) == {"pushups": 15}
    finally:
        coach.work_finished()
        await server.close()
