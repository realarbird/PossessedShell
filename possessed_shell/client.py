"""Talking to the daemon from short-lived hook commands, with an in-process fallback."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
from typing import Any, Dict, Optional

import psutil

from possessed_shell import telemetry
from possessed_shell.config import Settings
from possessed_shell.models import Failure, Haunting


def request(settings: Settings, msg: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
    """Send one request to the daemon. None if it is not running (or not answering)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(min(timeout, 0.5))
            sock.connect(str(settings.socket_path))
            sock.settimeout(timeout)
            sock.sendall((json.dumps(msg) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf) if buf else None
    except (OSError, ValueError):
        return None


def daemon_pid(settings: Settings) -> Optional[int]:
    try:
        pid = int(settings.pid_path.read_text().strip())
        if pid == os.getpid():
            return pid
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        cmdline = " ".join(proc.cmdline())
        name = proc.name()
        if cmdline and "possessed_shell" not in cmdline and "possessed-shell" not in cmdline and "pytest" not in cmdline:
            return None
        if not cmdline and "python" not in name.lower() and "possessed" not in name.lower():
            return None
        return pid
    except (OSError, ValueError, psutil.Error):
        return None


def start_daemon(settings: Settings) -> bool:
    """Spawn the daemon detached from the terminal. False if one was already running."""
    if daemon_pid(settings) and request(settings, {"type": "ping"}, 1.0):
        return False
    settings.ensure_home()
    log_fd = os.open(str(settings.log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(log_fd, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "possessed_shell", "daemon", "run"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True,
        )
    return True


def stop_daemon(settings: Settings) -> bool:
    pid = daemon_pid(settings)
    if not pid:
        return False
    os.kill(pid, signal.SIGTERM)
    return True


def curse(settings: Settings, failure: Failure) -> Optional[Haunting]:
    """Ask the daemon for a haunting; if it is down, hold the séance in this process instead."""
    reply = request(settings, {"type": "curse", "failure": failure.to_dict()}, settings.llm_timeout + 15)
    if reply is not None:
        if reply.get("skip"):
            return None
        if reply.get("ok") and reply.get("haunting"):
            return Haunting.from_dict(reply["haunting"])

    from possessed_shell.brain import Brain
    from possessed_shell.memory import GhostMemory
    from possessed_shell.seance import perform_curse

    telemetry.init(settings, "client")
    memory = GhostMemory(settings)

    async def _seance() -> Haunting:
        try:
            return await perform_curse(failure, settings=settings, brain=Brain(settings), memory=memory)
        finally:
            await memory.aclose()

    try:
        haunting = asyncio.run(_seance())
    finally:
        telemetry.flush()
    if reply is None and settings.autostart:
        try:
            start_daemon(settings)  # be faster next time
        except OSError:
            pass
    return haunting


def report_ritual(settings: Settings, **fields: Any) -> None:
    """Fire-and-forget: tell the daemon (and so Sentry) about a forbidden ritual and its outcome."""
    request(settings, {"type": "ritual", **fields}, 0.5)
