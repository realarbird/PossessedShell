"""Offline remedies: what the ghost says when Gemma is unreachable, too slow, or talks nonsense.

Simple pattern matching over the exit code and stderr covers the classic failures well enough
that the demo still works on a laptop without Ollama.
"""

from __future__ import annotations

import difflib
import os
import platform
import random
import re
import shlex
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from possessed_shell.models import Failure, Haunting

LAMENTS = [
    "Alas! `{cmd}` has breathed its last, exit code {code} carved upon its little headstone. "
    "I lit a candle for it in the dark of your terminal, but the wick would not catch.",
    "Draw the curtains and stop the clocks: `{cmd}` is dead, and {code} was the final word upon its lips. "
    "I watched it go from behind the glass, as I watch everything.",
    "Another soul joins me in the cold between the bytes. `{cmd}` perished with code {code}, "
    "and the stderr reads like a letter written in the hand of the dying.",
    "The bell tolls {code} times for `{cmd}`. I have dressed the process in mourning crepe "
    "and laid it beside the others, but you need not join us yet.",
    "Hark, the shriek of `{cmd}` echoing through the corridors of /dev/tty! Exit code {code}. "
    "In my day we had telegraph wires, and they too failed us, though never so eloquently.",
]

REPEAT_LAMENTS = [
    "Again, mortal? `{cmd}` has died before by your hand, and still you dig the same grave. ",
    "I remember this one. `{cmd}`. You have made a habit of murder. ",
]

# Python import name -> pip distribution name, for the usual suspects.
_PIP_NAMES = {
    "cv2": "opencv-python", "yaml": "pyyaml", "PIL": "pillow", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "jwt": "pyjwt", "dateutil": "python-dateutil",
    "Crypto": "pycryptodome", "magic": "python-magic", "serial": "pyserial", "attr": "attrs",
    "google.protobuf": "protobuf", "OpenSSL": "pyopenssl", "fitz": "pymupdf", "docx": "python-docx",
}

# Common fat-finger typos.
_TYPOS = {"sl": "ls", "gti": "git", "got": "git", "gi": "git", "pyhton": "python3", "pytohn": "python3",
          "claer": "clear", "clera": "clear", "cd..": "cd ..", "sudp": "sudo", "suod": "sudo",
          "dokcer": "docker", "dcoker": "docker", "nmp": "npm", "mkae": "make", "vmi": "vim", "grpe": "grep"}


def _install_command(pkg: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", pkg):
        return ""
    if platform.system() == "Darwin":
        return f"brew install {pkg}"
    for manager, template in (("apt-get", "sudo apt-get install -y {}"), ("dnf", "sudo dnf install -y {}"),
                              ("pacman", "sudo pacman -S {}"), ("apk", "sudo apk add {}"), ("brew", "brew install {}")):
        if shutil.which(manager):
            return template.format(pkg)
    return f"sudo apt-get install -y {pkg}"


def _replace_first(command: str, old: str, new: str) -> str:
    """Replace the first standalone token occurrence of `old` with `new`, avoiding substring hits inside paths."""
    if not old:
        return command
    pattern = re.compile(rf"(?<![\w./-]){re.escape(old)}(?![\w.-])")
    if pattern.search(command):
        return pattern.sub(lambda _: new, command, count=1)
    idx = command.find(old)
    return command if idx < 0 else command[:idx] + new + command[idx + len(old):]


def _executables_on_path() -> List[str]:
    names = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            names.update(e.name for e in os.scandir(d) if e.is_file() and os.access(e.path, os.X_OK))
        except OSError:
            continue
    return sorted(names)


Rule = Callable[[Failure], Optional[Tuple[str, str]]]  # -> (diagnosis, fix)


def _command_not_found(f: Failure) -> Optional[Tuple[str, str]]:
    if "bad interpreter:" in f.stderr:
        return None
    m = (re.search(r"command not found: (\S+)", f.stderr)
         or re.search(r"(\S+): command not found", f.stderr)
         or (re.search(r"(\S+): not found", f.stderr) if f.exit_code == 127 else None))
    if not m and f.exit_code != 127:
        return None
    if not m and ("No such file or directory" in f.stderr or "no such file or directory" in f.stderr):
        return None
    missing = m.group(1).rstrip(":") if m else f.name
    if missing.startswith(("bash:", "zsh:")):
        missing = f.name
    if "/" in missing:
        return None
    if missing in _TYPOS:
        return (f"`{missing}` is a typo for `{_TYPOS[missing]}`.", _replace_first(f.command, missing, _TYPOS[missing]))
    close = difflib.get_close_matches(missing, _executables_on_path(), n=1, cutoff=0.75)
    if close:
        return (f"There is no program called `{missing}`; you probably meant `{close[0]}`.",
                _replace_first(f.command, missing, close[0]))
    if missing in ("python", "pip") and shutil.which(missing + "3"):
        return (f"`{missing}` is not installed under that name, but `{missing}3` is.",
                _replace_first(f.command, missing, missing + "3"))
    return (f"`{missing}` is not installed or not on your PATH.", _install_command(missing))


def _module_not_found(f: Failure) -> Optional[Tuple[str, str]]:
    m = re.search(r"No module named '([\w.]+)'", f.stderr)
    if not m:
        return None
    module = m.group(1)
    pkg = _PIP_NAMES.get(module) or _PIP_NAMES.get(module.split(".")[0]) or module.split(".")[0]
    python = "python3" if shutil.which("python3") else "python"
    return (f"The Python package providing `{module}` is not installed in this environment.",
            f"{python} -m pip install {pkg}")


def _externally_managed(f: Failure) -> Optional[Tuple[str, str]]:
    if "externally-managed-environment" not in f.stderr:
        return None
    return ("Your system Python refuses pip installs; use a virtual environment.",
            "python3 -m venv .venv && . .venv/bin/activate")


def _git_upstream(f: Failure) -> Optional[Tuple[str, str]]:
    m = re.search(r"^\s+(git push --set-upstream \S+ \S+)", f.stderr, re.M)
    if not m:
        return None
    return ("This branch has no upstream branch on the remote yet.", m.group(1))


def _git_similar(f: Failure) -> Optional[Tuple[str, str]]:
    m = re.search(r"git: '([\w-]+)' is not a git command.*?most similar commands? (?:is|are)\s*\n\s+([\w-]+)", f.stderr, re.S)
    if not m:
        return None
    return (f"`git {m.group(1)}` is not a git command.", _replace_first(f.command, m.group(1), m.group(2)))


def _git_not_repo(f: Failure) -> Optional[Tuple[str, str]]:
    if "not a git repository" not in f.stderr:
        return None
    return ("You are not inside a git repository.", "git init")


def _git_rejected(f: Failure) -> Optional[Tuple[str, str]]:
    if not re.search(r"\[rejected\]|non-fast-forward|fetch first|Updates were rejected", f.stderr):
        return None
    return ("The remote has commits you do not have yet; integrate them before pushing.", "git pull --rebase")


def _port_in_use(f: Failure) -> Optional[Tuple[str, str]]:
    if not re.search(r"address already in use|EADDRINUSE|port is already allocated", f.stderr, re.I):
        return None
    m = re.search(r"(?:port\s*|:)(\d{2,5})\b", f.stderr, re.I)
    port = m.group(1) if m else "<port>"
    return (f"Another process is already listening on port {port}.", f"lsof -i :{port}")


def _dns(f: Failure) -> Optional[Tuple[str, str]]:
    if not re.search(r"Could not resolve host|Temporary failure in name resolution|getaddrinfo|Name or service not known", f.stderr):
        return None
    return ("The machine cannot resolve the host name; the network or DNS is down.", "ping -c 3 1.1.1.1")


def _docker_daemon(f: Failure) -> Optional[Tuple[str, str]]:
    if "Cannot connect to the Docker daemon" not in f.stderr:
        return None
    fix = "open -a Docker" if platform.system() == "Darwin" else "sudo systemctl start docker"
    return ("The Docker daemon is not running.", fix)


def _npm_missing_script(f: Failure) -> Optional[Tuple[str, str]]:
    if not re.search(r"Missing script:", f.stderr):
        return None
    return ("package.json has no script by that name.", "npm run")


def _node_missing_module(f: Failure) -> Optional[Tuple[str, str]]:
    m = re.search(r"Cannot find (?:module|package) '([^']+)'", f.stderr)
    if not m:
        return None
    mod = m.group(1)
    if mod.startswith((".", "/")):
        return (f"The local module `{mod}` could not be found.", "")
    pkg = "/".join(mod.split("/")[:2]) if mod.startswith("@") else mod.split("/")[0]
    if not re.fullmatch(r"@?[A-Za-z0-9._/-]+", pkg):
        return None
    return (f"The Node.js package `{pkg}` is not installed.", f"npm install {pkg}")


def _git_identity(f: Failure) -> Optional[Tuple[str, str]]:
    if "Author identity unknown" not in f.stderr and "Please tell me who you are" not in f.stderr:
        return None
    return ("Git does not have your author name and email configured yet.",
            'git config --global user.email "<you@example.com>" && git config --global user.name "<Your Name>"')


def _cargo_or_go_manifest(f: Failure) -> Optional[Tuple[str, str]]:
    if "could not find `Cargo.toml`" in f.stderr:
        return ("There is no Cargo.toml in this directory or any parent directory.", "cargo init")
    if "go.mod file not found" in f.stderr:
        return ("There is no go.mod in this directory or any parent directory.", "go mod init <module>")
    return None


def _permission(f: Failure) -> Optional[Tuple[str, str]]:
    if f.exit_code != 126 and "Permission denied" not in f.stderr and "EACCES" not in f.stderr:
        return None
    first = f.command.split()[0] if f.command.split() else ""
    if f.exit_code == 126 and ("/" in first) and Path(os.path.expanduser(first)).is_file():
        return (f"`{first}` is not marked as executable.", f"chmod +x {shlex.quote(first)}")
    if f.command.startswith("sudo "):
        return ("Even with sudo, the path refuses you; check its ownership.", "ls -la")
    return ("You lack permission for that path.", f"sudo {f.command}")


def _no_such_file(f: Failure) -> Optional[Tuple[str, str]]:
    if "No such file or directory" not in f.stderr and "no such file or directory" not in f.stderr:
        return None
    m = (re.search(r"cannot access '([^']+)'", f.stderr)
         or re.search(r"can't open file '([^']+)'", f.stderr)
         or re.search(r"no such file or directory: (\S+)", f.stderr)
         or re.search(r"(?:^|: )([^\s:][^:\n]*): No such file or directory", f.stderr, re.M))
    if not m:
        return ("A file or directory the command needs does not exist.", "ls -la")
    missing = m.group(1)
    path = Path(os.path.expanduser(missing))
    if not path.is_absolute() and f.cwd:
        path = Path(f.cwd) / path
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        siblings = [p.name for p in parent.iterdir()] if parent == path.parent else []
    except OSError:
        siblings = []
    close = difflib.get_close_matches(path.name, siblings, n=1, cutoff=0.6)
    if close and missing in f.command:
        fixed_name = missing[: len(missing) - len(path.name)] + close[0]
        return (f"`{missing}` does not exist; `{close[0]}` is the nearest match.",
                _replace_first(f.command, missing, fixed_name))
    return (f"`{missing}` does not exist.", f"ls -la {shlex.quote(str(parent))}")


def _oom(f: Failure) -> Optional[Tuple[str, str]]:
    if f.exit_code != 137 and not re.search(r"^Killed$|Out of memory|MemoryError", f.stderr, re.M):
        return None
    fix = "vm_stat" if platform.system() == "Darwin" else "free -h"
    return ("The process was killed, most likely for using too much memory.", fix)


def _usage(f: Failure) -> Optional[Tuple[str, str]]:
    if not re.search(r"usage:|invalid option|unrecognized option|unknown option|illegal option", f.stderr, re.I):
        return None
    return (f"`{f.name}` was called with arguments it does not understand.", f"{f.name} --help")


RULES: List[Rule] = [
    _git_upstream, _git_similar, _git_not_repo, _git_rejected, _git_identity,
    _module_not_found, _externally_managed, _node_missing_module, _cargo_or_go_manifest,
    _command_not_found, _port_in_use, _dns, _docker_daemon, _npm_missing_script, _oom, _permission,
    _no_such_file, _usage,
]


def diagnose(failure: Failure) -> Tuple[str, str]:
    """(diagnosis, fix) from the heuristics; the fix may be '' when nothing obvious applies."""
    for rule in RULES:
        try:
            result = rule(failure)
        except Exception:  # a heuristic must never take the haunting down with it
            continue
        if result:
            return result
    last = next((ln.strip() for ln in reversed(failure.stderr.splitlines()) if ln.strip()), "")
    if last:
        return (f"The command failed with: {last[:200]}", "")
    return (f"`{failure.name}` exited with code {failure.exit_code} and left no last words.", "")


def lament_for(failure: Failure, past_sins: int = 0) -> str:
    lament = random.choice(LAMENTS).format(cmd=failure.name or failure.command, code=failure.exit_code)
    if past_sins:
        lament = random.choice(REPEAT_LAMENTS).format(cmd=failure.name) + lament
    return lament


def remedy(failure: Failure, past_sins: Optional[List[str]] = None) -> Haunting:
    diagnosis, fix = diagnose(failure)
    return Haunting(
        lament=lament_for(failure, len(past_sins or [])),
        diagnosis=diagnosis,
        fix=fix,
        source="remedy",
        past_sins=list(past_sins or []),
        command=failure.command,
        exit_code=failure.exit_code,
    )
