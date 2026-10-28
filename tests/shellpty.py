"""A tiny pty driver for end-to-end tests of the shell hooks (no pexpect needed)."""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\r")

PROMPT = "HAUNTED> "


class ShellPty:
    def __init__(self, argv: List[str], env: Dict[str, str], cwd: Path):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        self.buffer = ""

    def _pump(self, timeout: float) -> None:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            try:
                data = os.read(self.fd, 65536).decode("utf-8", errors="replace")
            except OSError:
                return
            self.buffer += data

    def expect(self, pattern: str, timeout: float = 30.0) -> str:
        """Wait for a regex in the (ANSI-stripped) output; return and consume everything up to it."""
        deadline = time.monotonic() + timeout
        regex = re.compile(pattern)
        while True:
            clean = _ANSI.sub("", self.buffer)
            m = regex.search(clean)
            if m:
                self.buffer = clean[m.end():]
                return clean[: m.end()]
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for {pattern!r}; got:\n{clean[-3000:]}")
            self._pump(0.1)

    def send(self, text: str) -> None:
        os.write(self.fd, text.encode())

    def sendline(self, line: str) -> None:
        self.send(line + "\r")

    def close(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except OSError:
            pass
        os.close(self.fd)


def hooked_env(tmp: Path, shell: str) -> Dict[str, str]:
    venv_bin = str(Path(sys.executable).parent)
    return {
        "HOME": str(tmp),
        "PATH": f"{venv_bin}:/usr/local/bin:/usr/bin:/bin",
        "TERM": "dumb",
        "POSSESSED_HOME": str(tmp / ".possessed"),
        "POSSESSED_NO_ANIMATION": "1",
        "POSSESSED_AUTOSTART": "0",
        "POSSESSED_LLM_URL": "http://127.0.0.1:9",  # no Gemma: the offline grimoire answers
        "POSSESSED_LLM_TIMEOUT": "2",
        "HISTFILE": "/dev/null",
        "LANG": "C.UTF-8",
        "SHELL": f"/bin/{shell}",
    }
