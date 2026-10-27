"""The things strangers hit first: a leftover coach, a busy port, no Docker, no camera."""

import asyncio
import io
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

import lifeline
import sandbox
from coach_core import Coach, PortInUse, WorkoutLog, port_free, start_http_server
from sandbox import docker_command, explain_docker_error, run_in_sandbox
from watcher.messages import NO_CAMERA, camera_problem, link_problem

ROOT = Path(__file__).parents[1]


def test_orphaned_when_reparented_or_a_launcher_dies():
    assert not lifeline.orphaned([10, 20], getppid=lambda: 10, is_alive=lambda pid: True)
    assert lifeline.orphaned([10, 20], getppid=lambda: 1, is_alive=lambda pid: True)
    assert lifeline.orphaned([10, 20], getppid=lambda: 10, is_alive=lambda pid: pid != 20)
    assert not lifeline.orphaned([], getppid=lambda: 1, is_alive=lambda pid: False)


def test_launchers_start_with_our_parent():
    chain = lifeline.launchers()
    assert chain and chain[0] == os.getppid()
    assert all(pid > 1 for pid in chain)


def test_child_exits_when_its_launcher_is_killed(tmp_path):
    """The real thing: a launcher (sh) starts a 'coach' that watches it; kill sh, the coach goes."""
    child = tmp_path / "child.py"
    child.write_text(
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import lifeline\n"
        "print('started', flush=True)\n"
        "asyncio.run(lifeline.watch(lambda: print('orphaned', flush=True), every=0.1))\n"
    )
    out = tmp_path / "out.txt"
    launcher = subprocess.Popen(
        ["sh", "-c", f"{sys.executable} {child} > {out} 2>&1 & echo $!; wait"],
        stdout=subprocess.PIPE, text=True, start_new_session=True,
    )  # fmt: skip
    child_pid = int(launcher.stdout.readline())
    deadline = time.monotonic() + 10
    while "started" not in (out.read_text() if out.exists() else "") and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        launcher.send_signal(signal.SIGKILL)
        launcher.wait()
        deadline = time.monotonic() + 10
        while lifeline.alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "orphaned" in out.read_text()
    finally:
        if lifeline.alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.fixture
def busy_port():
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        yield holder.getsockname()[1]


def test_port_free(busy_port):
    assert not port_free(busy_port)


async def test_busy_port_gives_instructions_not_a_stack_trace(busy_port, tmp_path):
    coach = Coach(lambda line: None, log=WorkoutLog(tmp_path / "log.jsonl"))
    with pytest.raises(PortInUse) as caught:
        await start_http_server(coach, port=busy_port)
    message = str(caught.value)
    assert f"kill $(lsof -tiTCP:{busy_port} -sTCP:LISTEN)" in message
    assert "GYAU_PORT" in message


async def test_sandbox_without_docker_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    result = await run_in_sandbox("print(1)", log_path=tmp_path / "log.jsonl", charts_dir=tmp_path / "charts")
    assert "Docker isn't installed" in result.for_llm()["error"]
    assert not (tmp_path / "charts").exists()


def test_docker_errors_get_explained():
    assert "colima start" in explain_docker_error("Cannot connect to the Docker daemon at unix:///x. Is it running?")
    assert "docker build" in explain_docker_error("docker: Error response from daemon: No such image: gyau-sandbox:latest.")
    assert explain_docker_error("ZeroDivisionError: division by zero") == "ZeroDivisionError: division by zero"


def test_sandbox_never_pulls_images(tmp_path):
    cmd = docker_command("gyau-x", tmp_path / "log.jsonl", tmp_path / "out")
    assert cmd[cmd.index("--pull") + 1] == "never"


def test_camera_problem_messages():
    assert camera_problem(0, []) == NO_CAMERA
    assert "Privacy & Security" in NO_CAMERA
    wrong = camera_problem(3, [0, 1])
    assert "--camera 0" in wrong and "--list-cameras" in wrong


def test_link_problem_messages():
    coach = "http://127.0.0.1:7777"
    refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    assert "lk agent console coach.py" in link_problem(refused, coach)
    vision_off = urllib.error.HTTPError(coach + "/frame", 503, "x", {}, io.BytesIO(b'{"error": "no vision model"}'))
    assert "--no-cloud" in link_problem(vision_off, coach)
    failed = urllib.error.HTTPError(coach + "/frame", 502, "x", {}, io.BytesIO(b'{"error": "rate limited"}'))
    assert "rate limited" in link_problem(failed, coach)
