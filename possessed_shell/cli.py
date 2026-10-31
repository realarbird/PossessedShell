"""`possessed-shell`: the command the shell hooks call, plus setup and demo commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import termios
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator, List, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.text import Text

from possessed_shell import __version__, client, config, render, rituals
from possessed_shell.models import Failure
from possessed_shell.seance import should_haunt

HOOKS_DIR = Path(__file__).parent / "hooks"
STDERR_TAIL_BYTES = 4096
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


def _command(parts: List[str]) -> str:
    """The hooks pass the command line as one argument; a hand-typed `-- cmd args` gets re-quoted."""
    if parts and parts[0] == "--":
        parts = parts[1:]
    if len(parts) == 1:
        return parts[0].strip()
    return shlex.join(parts)


def read_stderr_tail(path: Optional[str], command: str = "") -> str:
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - STDERR_TAIL_BYTES))
            raw = f.read()
    except OSError:
        return ""
    text = _ANSI.sub("", raw.decode("utf-8", errors="replace")).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if size > STDERR_TAIL_BYTES and len(lines) > 1:
        lines = lines[1:]  # drop the partial first line
    if lines and command and lines[0].strip().endswith(command.strip()):
        lines = lines[1:]  # bash's readline echo can sneak in ahead of the command's stderr
    return "\n".join(lines).strip("\n")


# ---- hook commands ------------------------------------------------------------------------------

def haunt(settings: config.Settings, failure: Failure) -> int:
    """Show the ghost's reaction to a failure and leave its fix for the shell hook to pre-fill."""
    if not should_haunt(failure, settings):
        return 0
    console = render.make_console(settings.animation, settings.typewriter_cps)
    try:
        with render.summoning(console):
            haunting = client.curse(settings, failure)
        if haunting is None:
            return 0
        if haunting.fix and failure.session:
            fix_path = settings.session_dir(failure.session) / "fix"
            if fix_path.parent.is_dir():
                fix_path.write_text(haunting.fix)
        # The "press Up / Enter" hint only applies when a shell hook will pick the fix up.
        render.render_haunting(console, haunting, shell=failure.shell if failure.session else "")
    except KeyboardInterrupt:
        console.print()
        render.say(console, "The spirit recedes into the walls... for now.")
    return 0


def cmd_curse(args: argparse.Namespace, settings: config.Settings) -> int:
    command = _command(args.command)
    duration = args.duration
    if duration is None and args.started:
        try:
            duration = max(0.0, time.time() - float(args.started))
        except ValueError:
            duration = 0.0
    return haunt(settings, Failure(
        command=command,
        exit_code=args.exit_code,
        stderr=read_stderr_tail(args.stderr_file, command),
        cwd=args.cwd or os.getcwd(),
        shell=args.shell or "",
        session=args.session or "",
        duration=duration or 0.0,
    ))


@contextmanager
def cooked_tty() -> Iterator[Optional[Tuple[IO[str], IO[str]]]]:
    """Open /dev/tty in ordinary line mode (zsh's line editor leaves it raw while a widget runs)."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        yield None
        return
    old = None
    try:
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[0] |= termios.ICRNL
        new[1] |= termios.OPOST | termios.ONLCR
        new[3] |= termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN
        termios.tcsetattr(fd, termios.TCSANOW, new)
    except termios.error:
        pass
    reader = os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    writer = os.fdopen(os.dup(fd), "w", buffering=1, encoding="utf-8", errors="replace")
    try:
        yield reader, writer
    finally:
        writer.close()
        if old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, old)
            except termios.error:
                pass
        reader.close()


def cmd_ritual(args: argparse.Namespace, settings: config.Settings) -> int:
    if settings.disabled or not settings.rituals:
        return 0
    command = _command(args.command)
    ritual = rituals.analyze(command)
    if ritual is None:
        return 0
    with cooked_tty() as tty:
        if tty is None:
            return 0  # nobody to ask; never block blind
        tty_in, tty_out = tty
        console = Console(file=tty_out, highlight=False)
        console.animate = settings.animation  # type: ignore[attr-defined]
        console.cps = settings.typewriter_cps  # type: ignore[attr-defined]
        decision = "banished"
        try:
            render.render_ritual(console, ritual)
            if ritual.severity == "apocalyptic":
                question = "  Do you truly wish to summon such destruction? Type 'summon' to proceed: "
            else:
                question = "  Do you truly wish to summon such destruction? [y/N] "
            console.print(Text(question, style=f"bold {render.BLOOD}"), end="")
            tty_out.flush()
            answer = tty_in.readline().strip().lower()
            if (ritual.severity == "apocalyptic" and answer == "summon") or (
                    ritual.severity != "apocalyptic" and answer in ("y", "yes")):
                decision = "summoned"
        except KeyboardInterrupt:
            console.print()
        if decision == "summoned":
            render.say(console, "So be it. The candles are lit; the ritual begins.", f"bold {render.BLOOD}")
        else:
            render.say(console, "The circle is broken. The spirits rest, and so does your command.", f"italic {render.CYAN}")
        console.print()
    client.report_ritual(settings, command=command, name=ritual.name, severity=ritual.severity,
                         decision=decision, shell=getattr(args, "shell", None) or "")
    return 0 if decision == "summoned" else config.RITUAL_BANISHED


def cmd_omen(args: argparse.Namespace, settings: config.Settings) -> int:
    console = render.make_console(settings.animation, settings.typewriter_cps)
    if args.demo:
        from possessed_shell.ectoplasm import EctoplasmMonitor, Reading

        settings.omen_cooldown = 0
        monitor = EctoplasmMonitor(settings, read=lambda: Reading(97.0, 93.0, {"name": "chrome", "pid": 6666, "memory_percent": 41.0}))
        omens = []
        for _ in range(EctoplasmMonitor.SUSTAINED):
            omens = monitor.sample()
        for omen in omens[:1]:
            render.render_omen(console, omen)
        return 0
    if not args.session:
        return 0
    path = settings.session_dir(args.session) / "omen.json"
    try:
        raw = path.read_text()
    except OSError:
        return 0
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        omen = json.loads(raw)
    except ValueError:
        return 0
    if settings.omens:
        try:
            render.render_omen(console, omen)
        except KeyboardInterrupt:
            console.print()
    return 0


# ---- setup commands -----------------------------------------------------------------------------

def _self_command() -> List[str]:
    argv0 = Path(sys.argv[0])
    if argv0.name == "possessed-shell":
        if argv0.exists():
            return [str(argv0.resolve())]
        found = shutil.which("possessed-shell")
        if found:
            return [str(Path(found).resolve())]
    return [sys.executable, "-m", "possessed_shell"]


def hook_script(shell: str) -> str:
    template = (HOOKS_DIR / f"possessed.{shell}").read_text()
    assert "'" not in rituals.PREFILTER_ERE
    return (template.replace("__POSSESSED_BIN__", " ".join(shlex.quote(w) for w in _self_command()))
            .replace("__POSSESSED_RITUAL_RE__", rituals.PREFILTER_ERE))


def cmd_init(args: argparse.Namespace, settings: config.Settings) -> int:
    sys.stdout.write(hook_script(args.shell))
    return 0


def cmd_daemon(args: argparse.Namespace, settings: config.Settings) -> int:
    console = Console(highlight=False)
    if args.action == "run":
        from possessed_shell import daemon

        daemon.run(settings)
        return 0
    if args.action in ("stop", "restart"):
        stopped = client.stop_daemon(settings)
        if not args.quiet:
            console.print("The ghost is laid to rest." if stopped else "No ghost was haunting this machine.")
        if args.action == "stop":
            return 0
        for _ in range(30):
            if client.daemon_pid(settings) is None:
                break
            time.sleep(0.1)
    if args.action in ("start", "restart"):
        started = client.start_daemon(settings)
        if not args.quiet:
            console.print(f"The ghost awakens. (logs: {settings.log_path})" if started else "The ghost already haunts this machine.")
        return 0
    reply = client.request(settings, {"type": "ping"}, 2.0)
    if not reply:
        console.print("[bold red]No ghost is haunting this machine.[/] Start one with: possessed-shell daemon start")
        return 1
    console.print(f"Haunting (pid {reply['pid']}) · model {reply['model']} · sentry "
                  f"{'on' if reply.get('sentry') else 'off'} · backboard memory {'on' if reply.get('memory') else 'off'}")
    return 0


def cmd_doctor(args: argparse.Namespace, settings: config.Settings) -> int:
    from possessed_shell.brain import Brain
    from possessed_shell.memory import GhostMemory

    console = Console(highlight=False)
    table = Table(title="PossessedShell séance check", title_style=f"bold {render.BLOOD}", border_style=render.DEEP_BLOOD)
    table.add_column("", width=2)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    ok_all = True

    def row(ok: Optional[bool], name: str, status: str) -> None:
        nonlocal ok_all
        mark = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[yellow]·[/]"}[ok]
        ok_all = ok_all and ok is not False
        table.add_row(Text.from_markup(mark), Text(name), Text(status))

    row(sys.version_info >= (3, 9), "Python", sys.version.split()[0])
    ok, msg = asyncio.run(Brain(settings).status())
    row(ok, f"Brain ({settings.llm_api})", msg)
    reply = client.request(settings, {"type": "ping"}, 2.0)
    row(bool(reply), "Daemon", f"running (pid {reply['pid']})" if reply else "not running: possessed-shell daemon start")
    hooked = os.environ.get("POSSESSED_HOOKED")
    row(bool(hooked) or None, "Shell hook", f"loaded ({hooked})" if hooked else
        'not loaded in this shell: eval "$(possessed-shell init zsh)"  (or bash)')
    row(True if settings.sentry_dsn else None, "Sentry", "on" if settings.sentry_dsn else "off (set SENTRY_DSN to trace failures)")
    if settings.backboard_api_key:
        try:
            import backboard  # noqa: F401

            memory = GhostMemory(settings)

            async def _check_memory() -> str:
                try:
                    return await asyncio.wait_for(memory.assistant_id(), 15)
                finally:
                    await memory.aclose()

            assistant = asyncio.run(_check_memory())
            row(True, "Backboard memory", f"on (assistant {assistant[:8]}…)")
        except Exception as exc:
            row(False, "Backboard memory", f"BACKBOARD_API_KEY set but failed: {exc}")
    else:
        row(None, "Backboard memory", "off (set BACKBOARD_API_KEY to let the ghost remember)")
    capture = os.environ.get("POSSESSED_CAPTURE_STDERR", "1") != "0"
    row(None, "stderr capture", "on" if capture else "off")
    console.print(table)
    return 0 if ok_all else 1


def cmd_sins(args: argparse.Namespace, settings: config.Settings) -> int:
    from possessed_shell.memory import GhostMemory

    console = Console(highlight=False)
    if not settings.backboard_api_key:
        console.print("The ghost keeps no memories. Set BACKBOARD_API_KEY to let it remember your sins.")
        return 1
    memory = GhostMemory(settings)

    async def _sins():
        try:
            return await memory.sins(args.command or "", args.limit)
        finally:
            await memory.aclose()

    sins = asyncio.run(_sins())
    if not sins:
        console.print("Your slate is clean. For now.")
        return 0
    table = Table(title="Your sins, as the ghost remembers them", title_style=f"bold {render.BLOOD}",
                  border_style=render.DEEP_BLOOD, show_lines=True)
    table.add_column("Program", style=f"bold {render.CANDLE}")
    table.add_column("Exit", justify="right")
    table.add_column("What the ghost remembers", style=render.CYAN)
    for sin in sins:
        table.add_row(str(sin.get("command_name", "?")), str(sin.get("exit_code", "?")), sin["content"])
    console.print(table)
    return 0


def cmd_summon(args: argparse.Namespace, settings: config.Settings) -> int:
    """Run a command that is doomed to fail and let the ghost react, without any shell hook."""
    command = _command(args.command) or "ls /crypt/of/nothing"
    if cmd_ritual(argparse.Namespace(command=[command], shell=Path(os.environ.get("SHELL") or "/bin/sh").name), settings) == config.RITUAL_BANISHED:
        return config.RITUAL_BANISHED
    shell = os.environ.get("SHELL") or "/bin/sh"
    started = time.time()
    proc = subprocess.run([shell, "-c", command], stderr=subprocess.PIPE, text=True, errors="replace")
    sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        Console().print("That command lived. Summon something doomed, e.g.  possessed-shell summon -- python3 -c 'import ectoplasm'")
        return 0
    clean_stderr = _ANSI.sub("", proc.stderr).replace("\r\n", "\n").replace("\r", "\n")
    return haunt(settings, Failure(
        command=command,
        exit_code=proc.returncode,
        stderr=clean_stderr[-STDERR_TAIL_BYTES:],
        cwd=os.getcwd(),
        shell=Path(shell).name,
        duration=time.time() - started,
    ))


# ---- entry point --------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="possessed-shell", description="A Victorian ghost haunting your terminal.")
    p.add_argument("--version", action="version", version=f"possessed-shell {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="print the shell hook: eval \"$(possessed-shell init zsh)\"")
    s.add_argument("shell", choices=["zsh", "bash"])
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("curse", help="(hook) haunt a failed command")
    s.add_argument("--exit-code", type=int, required=True)
    s.add_argument("--session")
    s.add_argument("--shell")
    s.add_argument("--duration", type=float)
    s.add_argument("--started", help="epoch seconds when the command started")
    s.add_argument("--stderr-file")
    s.add_argument("--cwd")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_curse)

    s = sub.add_parser("ritual", help="(hook) warn about a destructive command; exit 66 = do not run it")
    s.add_argument("--session")
    s.add_argument("--shell")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_ritual)

    s = sub.add_parser("omen", help="(hook) show a pending ectoplasm omen")
    s.add_argument("--session")
    s.add_argument("--demo", action="store_true", help="show a sample omen")
    s.set_defaults(func=cmd_omen)

    s = sub.add_parser("daemon", help="start/stop/status/run the background ghost")
    s.add_argument("action", choices=["start", "stop", "restart", "status", "run"])
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_daemon)

    s = sub.add_parser("doctor", help="check Ollama/Gemma, the daemon, the hook, Sentry and Backboard")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sins", help="list the failures the ghost remembers (Backboard)")
    s.add_argument("--command", help="only this program")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_sins)

    s = sub.add_parser("summon", help="demo: run a doomed command and let the ghost react")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_summon)
    return p


# Commands the shell hooks run: they must never break the user's shell, whatever goes wrong.
_HOOK_COMMANDS = {"curse", "ritual", "omen"}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = config.load()
    try:
        return args.func(args, settings)
    except KeyboardInterrupt:
        return config.RITUAL_BANISHED if args.cmd == "ritual" else 130
    except Exception as exc:
        if args.cmd not in _HOOK_COMMANDS:
            raise
        sys.stderr.write(f"possessed-shell: the ghost stumbled ({exc.__class__.__name__}: {exc})\n")
        return 0
