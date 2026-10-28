"""The séance: what happens between a command dying and the ghost speaking.

Shared by the daemon and by the client's in-process fallback when the daemon is not running.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Dict, Optional, Set

from possessed_shell import ectoplasm, remedies, rituals, telemetry
from possessed_shell.brain import Brain, BrainError
from possessed_shell.config import Settings
from possessed_shell.memory import GhostMemory
from possessed_shell.models import Failure, Haunting
from possessed_shell.prompts import render_curse

# Exit codes that mean the user stopped the command, not that it died: SIGINT, SIGPIPE, SIGTSTP.
IGNORED_EXIT_CODES = {130, 141, 148}

# Programs whose exit status 1 is an answer ("no match", "files differ"), not an error.
_ANSWERS_WITH_ONE = {"grep", "egrep", "fgrep", "rg", "ag", "diff", "cmp", "test", "[", "[[", "false", "which",
                     "type", "pgrep", "command", "expr", "git-diff", "ack"}

STDERR_TAIL_CHARS = 3000


def should_haunt(failure: Failure, settings: Optional[Settings] = None) -> bool:
    if settings is not None and settings.disabled:
        return False
    if failure.exit_code == 0 or failure.exit_code in IGNORED_EXIT_CODES:
        return False
    if not failure.command.strip():
        return False
    if failure.exit_code == 1 and failure.name in _ANSWERS_WITH_ONE:
        return False
    return True


async def perform_curse(
    failure: Failure,
    *,
    settings: Settings,
    brain: Brain,
    memory: GhostMemory,
    background: Optional[Set["asyncio.Task"]] = None,
    snapshot: Callable[[str], Dict] = ectoplasm.snapshot,
) -> Haunting:
    """Trace the failure to Sentry, consult memory and Gemma, and return the ghost's verdict.

    `background` is the daemon's task set: when given, the Backboard write is fire-and-forget;
    otherwise (short-lived client) it is awaited so it is not cancelled on exit.
    """
    with telemetry.transaction(op="shell.curse", name=f"curse {failure.name or 'unknown'}") as txn:
        txn.set_tag("exit_code", str(failure.exit_code))
        txn.set_tag("command.name", failure.name)

        with telemetry.span("ghost.snapshot", "system vital signs"):
            state = snapshot(failure.cwd)
        event_id = telemetry.capture_failure(failure, state)

        with telemetry.span("ghost.memory.recall", "backboard recall") as sp:
            past_sins = await memory.recall(failure.name) if memory.enabled else []
            sp.set_data("past_sins", len(past_sins))

        prompt = render_curse(
            command=failure.command,
            exit_code=failure.exit_code,
            cwd=failure.cwd,
            shell=failure.shell,
            os_name=failure.os_name,
            duration=failure.duration,
            system_state=ectoplasm.describe(state),
            stderr=failure.stderr[-STDERR_TAIL_CHARS:],
            past_sins=past_sins,
        )

        haunting: Haunting
        with telemetry.span("ghost.llm", f"{settings.llm_api} {settings.model}") as sp:
            try:
                reply = await brain.haunt(prompt)
                haunting = Haunting(
                    lament=reply["lament"],
                    diagnosis=reply["diagnosis"] or "The spirits would not say.",
                    fix=reply["fix"],
                    source=settings.model,
                )
                sp.set_data("outcome", "gemma")
            except BrainError as exc:
                telemetry.breadcrumb(f"Gemma was silent: {exc}", category="ghost.llm")
                haunting = remedies.remedy(failure, past_sins)
                sp.set_data("outcome", "remedy")
                sp.set_data("error", str(exc))

        # A remedy that just re-runs the dead command is no remedy; borrow the grimoire's instead.
        if not haunting.fix or haunting.fix.strip() == failure.command.strip():
            haunting.fix = remedies.diagnose(failure)[1]

        dangerous = rituals.analyze(haunting.fix) if haunting.fix else None
        if dangerous:
            haunting.fix_warning = (f"Even the ghost's remedy is a forbidden ritual ({dangerous.name}). "
                                    "Read it twice before you run it.")

        haunting.command = failure.command
        haunting.exit_code = failure.exit_code
        haunting.past_sins = past_sins
        haunting.sentry_event_id = event_id
        txn.set_tag("haunting.source", haunting.source)

    if memory.enabled:
        write = memory.inscribe(failure, haunting)
        if background is not None:
            task = asyncio.ensure_future(write)
            background.add(task)
            task.add_done_callback(background.discard)
        else:
            await write
    return haunting
