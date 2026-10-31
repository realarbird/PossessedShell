"""The daemon: the ghost that lives in the walls.

Listens on a Unix socket for newline-delimited JSON from the shell hooks, keeps Gemma warm,
traces everything to Sentry and watches CPU/RAM for ectoplasm leaks.

Protocol (one JSON object per line, one request per connection):
  {"type": "ping"}                                  -> {"ok": true, "pid": ..., "model": ...}
  {"type": "curse", "failure": {...}}               -> {"ok": true, "haunting": {...}} | {"ok": true, "skip": true}
  {"type": "ritual", "command", "name", "severity", "decision", "shell"} -> {"ok": true}
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Dict, Optional, Set

import psutil

from possessed_shell import __version__, remedies, telemetry
from possessed_shell.brain import Brain, BrainError
from possessed_shell.config import Settings
from possessed_shell.ectoplasm import EctoplasmMonitor
from possessed_shell.memory import GhostMemory
from possessed_shell.models import Failure
from possessed_shell.seance import perform_curse, should_haunt

log = logging.getLogger("possessed.daemon")

MAX_REQUEST_BYTES = 256 * 1024


class Daemon:
    def __init__(self, settings: Settings, brain: Optional[Brain] = None, memory: Optional[GhostMemory] = None,
                 monitor: Optional[EctoplasmMonitor] = None):
        self.settings = settings
        self.brain = brain or Brain(settings)
        self.memory = memory or GhostMemory(settings)
        self.monitor = monitor or EctoplasmMonitor(settings)
        self.background: Set[asyncio.Task] = set()
        self._server: Optional[asyncio.AbstractServer] = None
        self._stopping = asyncio.Event()

    # ---- request handling -------------------------------------------------------------------

    async def dispatch(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        kind = msg.get("type")
        if kind == "ping":
            return {"ok": True, "pid": os.getpid(), "version": __version__, "model": self.settings.model,
                    "sentry": telemetry.enabled(), "memory": self.memory.enabled}

        if kind == "curse":
            failure = Failure.from_dict(msg.get("failure") or {})
            if not should_haunt(failure, self.settings):
                return {"ok": True, "skip": True}
            haunting = await perform_curse(failure, settings=self.settings, brain=self.brain,
                                           memory=self.memory, background=self.background)
            log.info("cursed %r (exit %s) via %s", failure.name, failure.exit_code, haunting.source)
            return {"ok": True, "haunting": haunting.to_dict()}

        if kind == "ritual":
            telemetry.capture_ritual(msg.get("command", ""), msg.get("name", "?"), msg.get("severity", "?"),
                                     msg.get("decision", "?"), msg.get("shell", ""))
            log.info("ritual %s %s", msg.get("name"), msg.get("decision"))
            return {"ok": True}

        return {"ok": False, "error": f"unknown request type {kind!r}"}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        msg: Dict[str, Any] = {}
        try:
            line = await reader.readline()
            if not line:
                return
            if len(line) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
            msg = json.loads(line)
            reply = await self.dispatch(msg)
        except Exception as exc:  # the ghost must never die of its own curse
            log.exception("request failed")
            telemetry.capture_exception(exc)
            reply = {"ok": False, "error": str(exc)}
            if msg.get("type") == "curse":
                try:
                    reply = {"ok": True, "haunting": remedies.remedy(Failure.from_dict(msg.get("failure") or {})).to_dict()}
                except Exception:
                    pass
        try:
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        except (ConnectionError, OSError):
            pass  # the client was banished (Ctrl-C) before we answered
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    # ---- background work --------------------------------------------------------------------

    async def warm_up(self) -> None:
        try:
            await self.brain.warm_up()
            log.info("%s is awake", self.settings.model)
        except BrainError as exc:
            log.warning("could not wake %s: %s", self.settings.model, exc)

    def deliver_omen(self, omen: Dict[str, Any]) -> int:
        """Drop the omen into every live shell session; the hook shows it at the next prompt."""
        delivered = 0
        sessions = self.settings.sessions_dir
        if not sessions.is_dir():
            return 0
        for session in sessions.iterdir():
            if not session.is_dir() or not session.name.isdigit():
                continue
            if not psutil.pid_exists(int(session.name)):
                _bury(session)
                continue
            try:
                tmp = session / ".omen.json.tmp"
                tmp.write_text(json.dumps(omen))
                os.replace(tmp, session / "omen.json")
                delivered += 1
            except OSError:
                continue
        return delivered

    async def monitor_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping.is_set():
            try:
                omens = await loop.run_in_executor(None, self.monitor.sample)
                for omen in omens:
                    telemetry.capture_omen(omen)
                    log.info("omen %s delivered to %d session(s)", omen["kind"], self.deliver_omen(omen))
            except Exception as exc:
                log.exception("ectoplasm monitor failed")
                telemetry.capture_exception(exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), self.settings.monitor_interval)
            except asyncio.TimeoutError:
                pass

    # ---- lifecycle ----------------------------------------------------------------------------

    async def serve(self, warm: bool = True) -> None:
        s = self.settings
        s.ensure_home()
        if s.socket_path.exists():
            s.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self.handle, path=str(s.socket_path))
        os.chmod(s.socket_path, 0o600)
        s.pid_path.write_text(str(os.getpid()))
        log.info("haunting %s (pid %d, model %s, sentry %s, memory %s)", s.socket_path, os.getpid(), s.model,
                 "on" if telemetry.enabled() else "off", "on" if self.memory.enabled else "off")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stopping.set)
            except (NotImplementedError, RuntimeError):
                pass

        tasks = []
        if warm:
            tasks.append(asyncio.ensure_future(self.warm_up()))
        if s.omens:
            tasks.append(asyncio.ensure_future(self.monitor_loop()))
        try:
            await self._stopping.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, *self.background, return_exceptions=True)
            await self.memory.aclose()
            for path in (s.socket_path, s.pid_path):
                try:
                    if path != s.pid_path or path.read_text().strip() == str(os.getpid()):
                        path.unlink()
                except OSError:
                    pass
            telemetry.flush()
            log.info("the ghost rests")

    def stop(self) -> None:
        self._stopping.set()


def _bury(session: Path) -> None:
    for child in session.iterdir():
        try:
            child.unlink()
        except OSError:
            pass
    try:
        session.rmdir()
    except OSError:
        pass


def run(settings: Settings) -> None:
    """Entry point for `possessed-shell daemon run`."""
    settings.ensure_home()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Several shells may wake the ghost at the same moment; only one may haunt.
    lock_fd = os.open(str(settings.home / "daemon.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    lock = os.fdopen(lock_fd, "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.info("another ghost already haunts %s", settings.home)
            return
        for session in settings.sessions_dir.iterdir():
            if session.is_dir() and session.name.isdigit() and not psutil.pid_exists(int(session.name)):
                _bury(session)
        telemetry.init(settings, "daemon")
        asyncio.run(Daemon(settings).serve())
    finally:
        lock.close()
