"""Shut the coach down when the terminal that started it goes away.

`lk agent console` runs the coach as a child process (lk → uv → python). Close the terminal and lk
dies, but the python process can keep running in the background: still holding the hooks' port,
still taking workouts from Claude Code, with nobody listening. This watches the processes that
launched the coach and ends it when they're gone.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable


def parent_of(pid: int) -> int | None:
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def launchers(depth: int = 2) -> list[int]:
    """Our parent, its parent, and so on, stopping before init/launchd (pid 1)."""
    chain, pid = [], os.getppid()
    while pid and pid > 1 and len(chain) < depth:
        chain.append(pid)
        pid = parent_of(pid)
    return chain


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


def orphaned(chain: list[int], *, getppid: Callable[[], int] = os.getppid, is_alive: Callable[[int], bool] = alive) -> bool:
    """True once our parent has changed (we were re-parented) or any launcher has exited."""
    if chain and getppid() != chain[0]:
        return True
    return any(not is_alive(pid) for pid in chain)


async def watch(on_orphaned: Callable[[], None], *, chain: list[int] | None = None, every: float = 2.0) -> None:
    chain = launchers() if chain is None else chain
    if not chain:
        return  # started straight from init: nothing to watch
    while not orphaned(chain):
        await asyncio.sleep(every)
    on_orphaned()
