"""A tiny pty driver for end-to-end tests of the shell hooks (no pexpect needed)."""

from __future__ import annotations

import codecs
import os
import platform
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\r")

PROMPT = "HAUNTED> "


_SPAWN_CTTY = (
    "import fcntl, os, sys, termios; "
    "os.setsid(); "
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0); "
    "os.execvp(sys.argv[1], sys.argv[1:])"
)


class ShellPty:
    def __init__(self, argv: List[str], env: Dict[str, str], cwd: Path):
        master, slave = pty.openpty()
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _SPAWN_CTTY, *argv],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=str(cwd),
                env=env,
                close_fds=True,
            )
        finally:
            os.close(slave)
        self.pid = self._proc.pid
        self.fd = master
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer = ""

    def _pump(self, timeout: float) -> None:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            try:
                data = self._decoder.decode(os.read(self.fd, 65536))
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
            self._proc.kill()
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def hooked_env(tmp: Path, shell: str) -> Dict[str, str]:
    venv_bin = str(Path(sys.executable).parent)
    shell_path = shutil.which(shell) or f"/bin/{shell}"
    lang = "en_US.UTF-8" if platform.system() == "Darwin" else "C.UTF-8"
    return {
        "HOME": str(tmp),
        "PATH": f"{venv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "TERM": "dumb",
        "POSSESSED_HOME": str(tmp / ".possessed"),
        "POSSESSED_NO_ANIMATION": "1",
        "POSSESSED_AUTOSTART": "0",
        "POSSESSED_LLM_URL": "http://127.0.0.1:9",  # no Gemma: the offline grimoire answers
        "POSSESSED_LLM_TIMEOUT": "2",
        "HISTFILE": "/dev/null",
        "BASH_SILENCE_DEPRECATION_WARNING": "1",
        "LANG": lang,
        "LC_ALL": lang,
        "SHELL": shell_path,
    }
