"""Data passed between the shell hook, the client, the daemon and the brain."""

from __future__ import annotations

import platform
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from possessed_shell.rituals import split_commands


def command_name(command: str) -> str:
    """The program a command line runs, e.g. 'sudo -E python3 x.py | tee' -> 'python3'."""
    for argv in split_commands(command):
        if argv:
            return argv[0]
    return command.split()[0] if command.split() else ""


@dataclass
class Failure:
    """A command that died: what the shell hook reports after a non-zero exit."""

    command: str
    exit_code: int
    stderr: str = ""
    cwd: str = ""
    shell: str = ""
    session: str = ""
    duration: float = 0.0
    os_name: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    timestamp: float = field(default_factory=time.time)

    @property
    def name(self) -> str:
        return command_name(self.command)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Failure":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        known["exit_code"] = int(known.get("exit_code", 1))
        return cls(**known)


@dataclass
class Haunting:
    """The ghost's answer to a failure."""

    lament: str
    diagnosis: str
    fix: str = ""
    source: str = "remedy"  # model name, or "remedy" for the offline heuristics
    fix_warning: str = ""  # set when the ghost's own fix is a forbidden ritual
    past_sins: List[str] = field(default_factory=list)
    sentry_event_id: Optional[str] = None
    command: str = ""
    exit_code: int = 0
    latency: float = 0.0  # seconds Gemma took to answer (0 for the grimoire)
    tokens: int = 0  # tokens Gemma generated, when the server says

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Haunting":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})
