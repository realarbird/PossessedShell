"""Settings, all read from environment variables so the shell hook can tweak them."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

# Exit code the `ritual` command uses to tell the shell hook "do NOT run this".
# Any other exit code lets the command through, so a broken install never bricks the shell.
RITUAL_BANISHED = 66


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _normalize_llm_url(raw: Optional[str] = None, *, default_port: Optional[int] = None) -> str:
    """Turn OLLAMA_HOST / POSSESSED_LLM_URL values (including bare host or 0.0.0.0:11434) into an HTTP URL."""
    if raw is None:
        explicit = os.environ.get("POSSESSED_LLM_URL", "").strip()
        if explicit:
            return _normalize_llm_url(explicit)
        ollama = os.environ.get("OLLAMA_HOST", "").strip()
        if ollama:
            return _normalize_llm_url(ollama, default_port=11434)
        return "http://127.0.0.1:11434"
    url = raw.strip()
    if not url:
        return "http://127.0.0.1:11434"
    if not re.match(r"^https?://", url, re.I):
        url = f"http://{url}"
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = parsed.port or default_port
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc = f"{netloc}:{port}"
    if parsed.username:
        auth = f"{parsed.username}:{parsed.password}" if parsed.password is not None else parsed.username
        netloc = f"{auth}@{netloc}"
    return urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), parsed.params, parsed.query, parsed.fragment))


@dataclass
class Settings:
    home: Path = field(default_factory=lambda: Path(os.environ.get("POSSESSED_HOME", "~/.possessed")).expanduser())

    # The local brain (Gemma 2 through Ollama, or any OpenAI-compatible local server such as an OpenClaw gateway).
    llm_api: str = field(default_factory=lambda: os.environ.get("POSSESSED_LLM_API", "ollama").lower())
    llm_url: str = field(default_factory=_normalize_llm_url)
    model: str = field(default_factory=lambda: os.environ.get("POSSESSED_MODEL", "gemma2:2b"))
    llm_timeout: float = field(default_factory=lambda: _float("POSSESSED_LLM_TIMEOUT", 60.0))

    # Telemetry and memory: both opt-in.
    sentry_dsn: str = field(default_factory=lambda: os.environ.get("SENTRY_DSN", ""))
    sentry_env: str = field(default_factory=lambda: os.environ.get("POSSESSED_ENV", "haunted"))
    backboard_api_key: str = field(default_factory=lambda: os.environ.get("BACKBOARD_API_KEY", ""))
    backboard_url: str = field(default_factory=lambda: os.environ.get("BACKBOARD_BASE_URL", "https://app.backboard.io/api"))

    # Features.
    disabled: bool = field(default_factory=lambda: _flag("POSSESSED_DISABLED", False))
    rituals: bool = field(default_factory=lambda: _flag("POSSESSED_RITUALS", True))
    omens: bool = field(default_factory=lambda: _flag("POSSESSED_OMENS", True))
    autostart: bool = field(default_factory=lambda: _flag("POSSESSED_AUTOSTART", True))
    animation: bool = field(default_factory=lambda: not _flag("POSSESSED_NO_ANIMATION", False))
    typewriter_cps: float = field(default_factory=lambda: _float("POSSESSED_TYPEWRITER_CPS", 90.0))

    # Ectoplasm leak detector thresholds (percent).
    cpu_threshold: float = field(default_factory=lambda: _float("POSSESSED_CPU_THRESHOLD", 90.0))
    mem_threshold: float = field(default_factory=lambda: _float("POSSESSED_MEM_THRESHOLD", 90.0))
    proc_mem_threshold: float = field(default_factory=lambda: _float("POSSESSED_PROC_MEM_THRESHOLD", 30.0))
    monitor_interval: float = field(default_factory=lambda: _float("POSSESSED_MONITOR_INTERVAL", 5.0))
    omen_cooldown: float = field(default_factory=lambda: _float("POSSESSED_OMEN_COOLDOWN", 600.0))

    @property
    def socket_path(self) -> Path:
        path = self.home / "daemon.sock"
        if len(str(path)) <= 100:
            return path
        # Unix socket paths are limited to ~104 bytes; deep home directories get a short stand-in.
        digest = hashlib.sha1(str(self.home).encode()).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"possessed-{os.getuid()}-{digest}.sock"

    @property
    def pid_path(self) -> Path:
        return self.home / "daemon.pid"

    @property
    def log_path(self) -> Path:
        return self.home / "daemon.log"

    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    def session_dir(self, session: str | int) -> Path:
        return self.sessions_dir / str(session)

    def ensure_home(self) -> None:
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.sessions_dir.mkdir(mode=0o700, parents=True, exist_ok=True)


def load() -> Settings:
    return Settings()
