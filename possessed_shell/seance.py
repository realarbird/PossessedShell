"""The séance: what happens between a command dying and the ghost speaking.

Shared by the daemon and by the client's in-process fallback when the daemon is not running.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, Optional, Set

from possessed_shell import ectoplasm, remedies, rituals, telemetry
from possessed_shell.brain import MAX_TOKENS, TEMPERATURE, Brain, BrainError
from possessed_shell.config import Settings
from possessed_shell.memory import GhostMemory
from possessed_shell.models import Failure, Haunting
from possessed_shell.prompts import GHOST_NAME, GHOST_SYSTEM_PROMPT, render_curse

# Exit codes that mean the user stopped the command, not that it died: SIGINT, SIGPIPE, SIGTSTP.
IGNORED_EXIT_CODES = {130, 141, 148}

# Programs whose exit status 1 is an answer ("no match", "files differ"), not an error.
_ANSWERS_WITH_ONE = {"grep", "egrep", "fgrep", "rg", "ag", "diff", "cmp", "test", "[", "[[", "false", "which",
                     "type", "pgrep", "command", "expr", "ack"}

STDERR_TAIL_CHARS = 3000


def should_haunt(failure: Failure, settings: Optional[Settings] = None) -> bool:
    if settings is not None and settings.disabled:
        return False
    if failure.exit_code == 0 or failure.exit_code in IGNORED_EXIT_CODES:
        return False
    if not failure.command.strip():
        return False
    if failure.exit_code == 1 and (failure.name in _ANSWERS_WITH_ONE or _is_git_diff_answer(failure.command)):
        return False
    return True


def _is_git_diff_answer(command: str) -> bool:
    """`git diff --exit-code` / `--quiet` exit 1 to say "there are differences"."""
    argv = next(iter(rituals.split_commands(command)), [])
    return argv[:2] == ["git", "diff"] and bool({"--exit-code", "--quiet"} & set(argv))


def _chat_span_data(reply: Dict[str, Any]) -> Dict[str, Any]:
    """Sentry AI agent attributes for Gemma's answer: what came back, how many tokens, how fast."""
    usage = reply.get("usage") or {}
    data: Dict[str, Any] = {
        "gen_ai.response.model": usage.get("response_model"),
        "gen_ai.response.text": json.dumps({k: reply.get(k) for k in ("lament", "diagnosis", "fix")}),
        "gen_ai.usage.input_tokens": usage.get("input_tokens"),
        "gen_ai.usage.output_tokens": usage.get("output_tokens"),
        "ollama.load_seconds": usage.get("load_seconds"),
        "ollama.tokens_per_second": usage.get("tokens_per_second"),
    }
    if "input_tokens" in usage and "output_tokens" in usage:
        data["gen_ai.usage.total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return {k: v for k, v in data.items() if v is not None}


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
            # psutil blocks (a 0.1s CPU sample, a walk over every process): keep the daemon's loop free.
            state = await asyncio.get_running_loop().run_in_executor(None, snapshot, failure.cwd)
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
        # Sentry AI agent tracing: one invoke_agent span for the ghost, one chat span for the Gemma call.
        with telemetry.span("gen_ai.invoke_agent", f"invoke_agent {GHOST_NAME}") as sp:
            sp.set_data("gen_ai.operation.name", "invoke_agent")
            sp.set_data("gen_ai.agent.name", GHOST_NAME)
            sp.set_data("gen_ai.system", settings.llm_api)
            sp.set_data("gen_ai.request.model", settings.model)
            try:
                with telemetry.span("gen_ai.chat", f"chat {settings.model}") as chat:
                    chat.set_data("gen_ai.operation.name", "chat")
                    chat.set_data("gen_ai.system", settings.llm_api)
                    chat.set_data("gen_ai.request.model", settings.model)
                    chat.set_data("gen_ai.request.temperature", TEMPERATURE)
                    chat.set_data("gen_ai.request.max_tokens", MAX_TOKENS)
                    chat.set_data("gen_ai.system_instructions", json.dumps([{"type": "text", "content": GHOST_SYSTEM_PROMPT}]))
                    chat.set_data("gen_ai.request.messages", json.dumps([{"role": "user", "content": prompt}]))
                    started = time.monotonic()
                    reply = await brain.haunt(prompt)  # a BrainError marks this span as failed
                    latency = time.monotonic() - started
                    for key, value in _chat_span_data(reply).items():
                        chat.set_data(key, value)
                haunting = Haunting(
                    lament=reply["lament"],
                    diagnosis=reply["diagnosis"] or "The spirits would not say.",
                    fix=reply["fix"],
                    source=settings.model,
                    latency=latency,
                    tokens=(reply.get("usage") or {}).get("output_tokens") or 0,
                )
                sp.set_data("outcome", "gemma")
            except BrainError as exc:
                telemetry.breadcrumb(f"Gemma was silent: {exc}", category="gen_ai.chat")
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
