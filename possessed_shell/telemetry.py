"""Sentry: tracing the daemon itself and every command that dies in the terminal.

Everything is a no-op until SENTRY_DSN is set. Events are scrubbed by redact.py before they leave.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import sentry_sdk

from possessed_shell import __version__
from possessed_shell.config import Settings
from possessed_shell.models import Failure
from possessed_shell.redact import redact, redact_obj

_enabled = False


class ShellCommandFailed(Exception):
    """A command in the haunted terminal exited non-zero."""


def _scrub(event: Dict[str, Any], hint: Dict[str, Any]) -> Dict[str, Any]:
    return redact_obj(event)


def init(settings: Settings, component: str, **overrides: Any) -> bool:
    """Start Sentry for the daemon or the client. Returns False (and stays silent) without a DSN."""
    global _enabled
    dsn = overrides.pop("dsn", None) or settings.sentry_dsn
    if not dsn:
        return False
    options: Dict[str, Any] = dict(
        dsn=dsn,
        release=f"possessed-shell@{__version__}",
        environment=settings.sentry_env,
        traces_sample_rate=1.0,
        max_breadcrumbs=50,
        before_send=_scrub,
        before_send_transaction=_scrub,
    )
    if component == "daemon":
        from sentry_sdk.integrations.asyncio import AsyncioIntegration

        options["integrations"] = [AsyncioIntegration()]
    options.update(overrides)
    sentry_sdk.init(**options)
    sentry_sdk.set_tag("component", component)
    sentry_sdk.set_tag("llm.model", settings.model)
    _enabled = True
    return True


def enabled() -> bool:
    return _enabled


@contextmanager
def transaction(op: str, name: str) -> Iterator[Any]:
    with sentry_sdk.start_transaction(op=op, name=name) as txn:
        yield txn


@contextmanager
def span(op: str, name: str) -> Iterator[Any]:
    with sentry_sdk.start_span(op=op, name=name) as sp:
        yield sp


def capture_failure(failure: Failure, system_state: Dict[str, Any]) -> Optional[str]:
    """Record a dead command as a ShellCommandFailed exception event. Returns the Sentry event id."""
    if not _enabled:
        return None
    first_line = next((ln.strip() for ln in reversed(failure.stderr.splitlines()) if ln.strip()), "")
    message = f"`{failure.name}` exited with code {failure.exit_code}"
    if first_line:
        message += f": {first_line[:200]}"
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("shell", failure.shell or "unknown")
        scope.set_tag("exit_code", str(failure.exit_code))
        scope.set_tag("command.name", failure.name)
        scope.set_context("command", {
            "command": redact(failure.command),
            "exit_code": failure.exit_code,
            "cwd": failure.cwd,
            "shell": failure.shell,
            "session": failure.session,
            "duration_seconds": failure.duration,
            "os": failure.os_name,
            "stderr_tail": redact(failure.stderr[-1500:]),
        })
        scope.set_context("system_state", system_state)
        scope.fingerprint = ["shell-command-failed", failure.name, str(failure.exit_code)]
        if failure.stderr:
            scope.add_attachment(bytes=redact(failure.stderr).encode(), filename="stderr.txt", content_type="text/plain")
        return sentry_sdk.capture_exception(ShellCommandFailed(redact(message)))


def capture_ritual(command: str, name: str, severity: str, decision: str, shell: str = "") -> None:
    sentry_sdk.add_breadcrumb(category="ritual", message=f"{name}: {decision}", level="warning",
                              data={"command": redact(command), "severity": severity})
    if not _enabled:
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("ritual", name)
        scope.set_tag("ritual.severity", severity)
        scope.set_tag("ritual.decision", decision)
        scope.set_tag("shell", shell or "unknown")
        scope.set_context("ritual", {"command": redact(command), "name": name, "severity": severity, "decision": decision})
        sentry_sdk.capture_message(f"Forbidden ritual {decision}: {name}", level="warning")


def capture_omen(omen: Dict[str, Any]) -> None:
    sentry_sdk.add_breadcrumb(category="ectoplasm", message=omen.get("title", ""), level="warning",
                              data={"kind": omen.get("kind"), "value": omen.get("value")})
    if not _enabled:
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("omen.kind", omen.get("kind", "unknown"))
        scope.set_context("omen", {k: v for k, v in omen.items() if k != "message"})
        sentry_sdk.capture_message(f"Ectoplasm leak: {omen.get('detail') or omen.get('title')}", level="warning")


def breadcrumb(message: str, category: str = "ghost", **data: Any) -> None:
    sentry_sdk.add_breadcrumb(category=category, message=message, data=data or None)


def capture_exception(exc: BaseException) -> Optional[str]:
    return sentry_sdk.capture_exception(exc) if _enabled else None


def flush(timeout: float = 2.0) -> None:
    if _enabled:
        sentry_sdk.flush(timeout=timeout)
