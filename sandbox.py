"""Run coach-written Python in a throwaway Docker container.

The container has no network, a read-only filesystem, and exactly two mounts: the workout log
(read-only) and a fresh folder for charts (the only thing it can write to on the host).
Build the image once with:  docker build -t gyau-sandbox sandbox
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

IMAGE = os.environ.get("GYAU_SANDBOX_IMAGE", "gyau-sandbox")
CHARTS_DIR = Path.home() / ".get-your-ass-up" / "charts"
TIMEOUT = 30.0
MAX_OUTPUT_CHARS = 2000
MAX_ERROR_CHARS = 600


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    charts: list[Path] = field(default_factory=list)

    def for_llm(self) -> dict:
        result: dict = {"stdout": self.stdout}
        if self.timed_out:
            result["error"] = "Timed out and was killed. Write something faster."
        elif self.exit_code:
            # The end of a traceback is the useful part.
            result["error"] = self.stderr[-MAX_ERROR_CHARS:] or f"exit code {self.exit_code}"
        if self.charts:
            result["charts"] = [p.name for p in self.charts]
            result["note"] = "The charts are already open on the user's screen."
        return result


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def docker_command(name: str, log_path: Path, out_dir: Path, *, image: str = IMAGE) -> list[str]:
    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--memory", "512m",
        "--cpus", "1",
        "--pids-limit", "128",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        # Run as you, so the one writable mount (the charts folder) is actually writable.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{out_dir.resolve()}:/out:rw",
    ]  # fmt: skip
    if log_path.exists():
        cmd += ["-v", f"{log_path.resolve()}:/data/log.jsonl:ro"]
    return cmd + [image, "python", "-"]


async def run_in_sandbox(
    code: str,
    *,
    log_path: Path,
    charts_dir: Path = CHARTS_DIR,
    timeout: float = TIMEOUT,
    image: str = IMAGE,
) -> SandboxResult:
    out_dir = charts_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    out_dir.mkdir(parents=True)
    name = f"gyau-{out_dir.name}"

    proc = await asyncio.create_subprocess_exec(
        *docker_command(name, log_path, out_dir, image=image),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    result = SandboxResult()
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(code.encode()), timeout)
        result.exit_code = proc.returncode
    except TimeoutError:
        result.timed_out = True
        # Killing the docker CLI doesn't stop the container, so kill it by name.
        killer = await asyncio.create_subprocess_exec(
            "docker", "kill", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await killer.wait()
        proc.kill()
        await proc.wait()
        stdout, stderr = b"", b""

    result.stdout = truncate(stdout.decode(errors="replace").strip())
    result.stderr = truncate(stderr.decode(errors="replace").strip())
    result.charts = sorted(out_dir.glob("*.png"))
    if not any(out_dir.iterdir()):
        out_dir.rmdir()
    return result


async def open_on_screen(paths: list[Path]) -> None:
    """Pop charts up on the Mac (so they can be screen-recorded). No-op elsewhere."""
    if paths and shutil.which("open"):
        await (await asyncio.create_subprocess_exec("open", *map(str, paths))).wait()
