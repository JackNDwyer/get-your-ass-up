import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import sandbox
from sandbox import SandboxResult, docker_command, run_in_sandbox, truncate

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from seed_demo_log import demo_events, main as seed_main  # noqa: E402

# A stand-in `docker` that runs the code with the host Python, with OUT pointing at the folder
# that would be mounted at /out. Lets us test the plumbing without Docker.
FAKE_DOCKER = """#!/bin/sh
[ "$1" = kill ] && { echo "$2" >> "$(dirname "$0")/killed"; exit 0; }
while [ $# -gt 0 ]; do
  case "$1" in -v) case "$2" in *:/out:rw) OUT="${2%:/out:rw}";; esac; shift;; esac
  shift
done
OUT="$OUT" exec "PYTHON" -
"""


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER.replace("PYTHON", sys.executable))
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def test_docker_command_locks_the_box_down(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text("")
    cmd = docker_command("gyau-x", log, tmp_path / "out")
    joined = " ".join(cmd)
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "--rm", "--memory 512m"]:
        assert flag in joined
    mounts = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-v"]
    assert mounts == [f"{(tmp_path / 'out').resolve()}:/out:rw", f"{log.resolve()}:/data/log.jsonl:ro"]
    assert cmd[-3:] == ["gyau-sandbox", "python", "-"]


def test_docker_command_skips_missing_log(tmp_path):
    cmd = docker_command("gyau-x", tmp_path / "nope.jsonl", tmp_path / "out")
    assert sum(arg == "-v" for arg in cmd) == 1


def test_truncate():
    assert truncate("short") == "short"
    out = truncate("x" * 5000, limit=100)
    assert out.startswith("x" * 100) and out.endswith("[truncated 4900 chars]")


def test_result_for_llm():
    assert SandboxResult(stdout="3", exit_code=0).for_llm() == {"stdout": "3"}
    failed = SandboxResult(stderr="Traceback...\n" + "y" * 1000 + "\nNameError: x", exit_code=1).for_llm()
    assert failed["error"].endswith("NameError: x") and len(failed["error"]) <= sandbox.MAX_ERROR_CHARS
    assert "error" in SandboxResult(timed_out=True).for_llm()
    charted = SandboxResult(exit_code=0, charts=[Path("/c/pushups.png")]).for_llm()
    assert charted["charts"] == ["pushups.png"]


async def test_runs_code_and_returns_stdout(fake_docker, tmp_path):
    r = await run_in_sandbox('print("strawberry".count("r"))', log_path=tmp_path / "log", charts_dir=tmp_path)
    assert (r.stdout, r.exit_code, r.charts) == ("3", 0, [])
    assert list(tmp_path.glob("2*")) == []  # empty output folders are cleaned up


async def test_collects_charts(fake_docker, tmp_path):
    code = 'import os; open(os.environ["OUT"] + "/chart.png", "wb").write(b"png")'
    r = await run_in_sandbox(code, log_path=tmp_path / "log", charts_dir=tmp_path / "charts")
    assert [p.name for p in r.charts] == ["chart.png"]
    assert r.charts[0].parent.parent == tmp_path / "charts"


async def test_reports_errors(fake_docker, tmp_path):
    r = await run_in_sandbox("1/0", log_path=tmp_path / "log", charts_dir=tmp_path)
    assert r.exit_code == 1
    assert "ZeroDivisionError" in r.for_llm()["error"]


async def test_caps_stdout(fake_docker, tmp_path):
    r = await run_in_sandbox('print("x" * 100_000)', log_path=tmp_path / "log", charts_dir=tmp_path)
    assert len(r.stdout) < sandbox.MAX_OUTPUT_CHARS + 50


async def test_timeout_kills_container(fake_docker, tmp_path):
    r = await run_in_sandbox("import time; time.sleep(30)", log_path=tmp_path / "log", charts_dir=tmp_path, timeout=0.5)
    assert r.timed_out
    assert "Timed out" in r.for_llm()["error"]
    assert (fake_docker / "killed").read_text().startswith("gyau-")


def test_seed_is_two_weeks_in_a_separate_file(tmp_path):
    events = demo_events(date(2026, 10, 24))
    days = {datetime.fromisoformat(e["ts"]).date() for e in events}
    assert min(days) >= date(2026, 10, 24) - timedelta(days=13)
    assert {"reps", "assigned"} <= {e["type"] for e in events}

    target = tmp_path / "demo.jsonl"
    seed_main(target)
    assert len(target.read_text().splitlines()) == len(demo_events(date.today()))
    json.loads(target.read_text().splitlines()[0])


def _docker_image_ready() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", sandbox.IMAGE], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _docker_image_ready(), reason="needs Docker and the gyau-sandbox image")
async def test_real_sandbox_has_no_network():
    charts = Path.home() / ".get-your-ass-up" / "charts"  # must live under $HOME for Colima
    code = "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    print('online')\nexcept OSError:\n    print('offline')"
    r = await run_in_sandbox(code, log_path=Path("/nonexistent"), charts_dir=charts)
    assert r.stdout == "offline"
