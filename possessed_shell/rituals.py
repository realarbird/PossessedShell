"""The Forbidden Ritual Interceptor: spotting destructive commands before they run.

The shell hook runs a cheap regex (PREFILTER_ERE) on every command line and only starts
Python when it matches. `analyze()` then makes the precise call on the parsed command.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Iterator, List, Optional

SEVERITY_ORDER = {"grave": 1, "dire": 2, "apocalyptic": 3}

# POSIX ERE run by the shell hook (`[[ ${cmd,,} =~ $re ]]` in bash, `${(L)BUFFER}` in zsh) against
# the lower-cased command line. It must match every line `analyze()` flags; false positives only
# cost a Python start-up. tests/test_rituals.py checks this with a real bash.
PREFILTER_ERE = (
    r"(^|[^[:alnum:]_.-])(rm|dd|shred|chmod|chown|dropdb)([[:space:]]|$)"
    r"|mkfs"
    r"|git.*(push|reset|clean)"
    r"|kubectl.*delete"
    r"|prune|destroy|truncate|flush(all|db)|dropdatabase"
    r"|drop[[:space:]]+(database|schema|table)"
    r"|delete[[:space:]]+from"
    r"|find.*-delete"
    r"|:\(\)"
    r"|\|[[:space:]]*(sudo[[:space:]]+)?(ba|z|da)?sh([[:space:]]|$)"
    r"|/dev/(sd|nvme|disk|hd)"
)

# Paths whose recursive deletion (or chmod) is the end of days rather than a mere misfortune.
_SACRED_PATHS = {
    "/", "/*", "~", "~/", "~/*", "$home", "${home}", "$home/", "*", ".", "./", "..", "../", ".*",
    "/home", "/etc", "/usr", "/var", "/bin", "/boot", "/lib", "/opt", "/root", "/system", "/users",
}

_PREFIX_COMMANDS = {"sudo", "doas", "command", "builtin", "exec", "nohup", "time", "nice", "env", "xargs", "caffeinate"}
_OPERATORS = {";", "&&", "||", "|", "&", ";;", "|&", "(", ")"}


@dataclass
class Ritual:
    name: str
    severity: str  # grave | dire | apocalyptic
    lore: str  # the ghost's warning, in Victorian prose
    consequence: str  # plain-English explanation of what will happen
    command: str

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER[self.severity]


def split_commands(line: str) -> List[List[str]]:
    """Split a command line into simple commands (argv lists), ignoring quoting edge cases."""
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a crude split
        tokens = re.split(r"\s+", line.strip())

    commands: List[List[str]] = []
    current: List[str] = []
    for tok in tokens:
        if tok in _OPERATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return [_strip_prefixes(c) for c in commands if c]


def _strip_prefixes(argv: List[str]) -> List[str]:
    """Remove env assignments and wrappers such as sudo/nohup/env so argv[0] is the real program."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        base = os.path.basename(tok.lstrip("\\"))
        if base in _PREFIX_COMMANDS:
            i += 1
            # skip the wrapper's own flags (and the argument of the ones that take one)
            while i < len(argv) and argv[i].startswith("-"):
                takes_arg = argv[i] in ("-u", "-g", "-C", "-D", "-n", "-p", "-I", "-L")
                i += 2 if takes_arg else 1
            continue
        break
    rest = argv[i:]
    if rest:
        rest = [os.path.basename(rest[0].lstrip("\\"))] + rest[1:]
    return rest


def _short_flags(args: List[str]) -> str:
    """All single-dash flag letters, e.g. ['-rf', '-v'] -> 'rfv'."""
    return "".join(a[1:] for a in args if re.fullmatch(r"-[A-Za-z]+", a))


def _targets(args: List[str]) -> List[str]:
    return [a for a in args if not a.startswith("-")]


def _is_sacred(path: str) -> bool:
    p = path.lower().rstrip("/") or "/"
    return path.lower() in _SACRED_PATHS or p in _SACRED_PATHS


def _check_simple(argv: List[str], line: str) -> Iterator[Ritual]:
    prog, args = argv[0], argv[1:]
    flags = _short_flags(args)

    if prog == "rm":
        recursive = "r" in flags.lower() or "--recursive" in args
        force = "f" in flags or "--force" in args
        if recursive and force:
            sacred = [t for t in _targets(args) if _is_sacred(t)]
            if sacred or "--no-preserve-root" in args:
                yield Ritual(
                    "rm -rf upon sacred ground", "apocalyptic",
                    "You would unmake the very ground upon which this machine stands. Not one file shall "
                    "rise again, not even at the Last Trump.",
                    f"Recursively and forcibly deletes {', '.join(sacred) or 'the root filesystem'} with no confirmation and no undo.",
                    line,
                )
            else:
                yield Ritual(
                    "rm -rf", "dire",
                    "You reach for the gravedigger's spade. What you bury with -rf is buried without a "
                    "headstone, and no séance can summon it back.",
                    f"Recursively and forcibly deletes {', '.join(_targets(args)) or 'the given paths'}. There is no trash bin.",
                    line,
                )

    elif prog == "git" and args:
        sub_i = next((i for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] not in ("-C", "-c"))), None)
        sub = args[sub_i] if sub_i is not None else ""
        rest = args[sub_i + 1:] if sub_i is not None else []
        if sub == "push":
            forced = (
                "--force" in rest
                or "f" in _short_flags(rest)
                or any(a.startswith("+") for a in rest)
                or "--mirror" in rest
                or "--delete" in rest
            )
            if forced:
                yield Ritual(
                    "git push --force", "dire",
                    "To force-push is to dig up another's grave and lay your own corpse in it. Their commits "
                    "shall wander the reflog evermore, nameless and unmourned.",
                    "Overwrites the remote branch history; collaborators' commits that are not in your branch are lost from it.",
                    line,
                )
        elif sub == "reset" and "--hard" in rest:
            yield Ritual(
                "git reset --hard", "dire",
                "You would drown every uncommitted thought in the black waters of the Thames. They float not back.",
                "Discards all uncommitted changes in tracked files permanently.",
                line,
            )
        elif sub == "clean" and ("f" in _short_flags(rest) or "--force" in rest):
            yield Ritual(
                "git clean -f", "dire",
                "The untracked foundlings of your workhouse, cast out into the snow with no name in any ledger.",
                "Permanently deletes untracked files (and directories with -d) that git has never saved.",
                line,
            )

    elif prog in ("chmod", "chown"):
        recursive = "R" in flags or "--recursive" in args
        targets = _targets(args)
        if prog == "chmod" and any(re.fullmatch(r"0?777|a\+rwx|ugo\+rwx|o\+w|a\+w", t) for t in targets):
            sacred = any(_is_sacred(t) for t in targets)
            yield Ritual(
                "chmod 777", "apocalyptic" if (recursive and sacred) else "grave",
                "You fling open every door and window of the manor at midnight. Any wandering spirit, or any "
                "wandering process, may now take up residence.",
                "Makes the files readable, writable and executable by every user on the system.",
                line,
            )
        elif recursive and any(_is_sacred(t) for t in targets):
            yield Ritual(
                f"{prog} -R upon sacred ground", "apocalyptic",
                "You would change the locks on every crypt in the cemetery at once. The rightful dead shall be shut out.",
                f"Recursively changes ownership/permissions of a system-critical path; the OS may stop booting.",
                line,
            )

    elif prog.startswith("mkfs"):
        yield Ritual(
            "mkfs", "apocalyptic",
            "You would salt the earth of an entire disk. Every name upon it shall be scoured from memory.",
            "Formats a filesystem, erasing everything on the target device.",
            line,
        )

    elif prog == "dd" and any(a.startswith("of=/dev/") for a in args):
        yield Ritual(
            "dd onto a raw device", "apocalyptic",
            "The disk-destroyer stirs. One misplaced letter and it devours the wrong drive whole, bones and all.",
            "Writes raw bytes directly over a block device, destroying its partitions and data.",
            line,
        )

    elif prog == "shred":
        yield Ritual(
            "shred", "dire",
            "Not merely death, but the scattering of ashes upon the wind. Nothing remains to mourn.",
            "Overwrites files repeatedly so they can never be recovered.",
            line,
        )

    elif prog == "find" and "-delete" in args:
        yield Ritual(
            "find -delete", "grave",
            "A reaper sent wandering the corridors with a list. Pray the list is the one you intended.",
            "Deletes every file the find expression matches, with no confirmation.",
            line,
        )

    elif prog == "kubectl" and "delete" in args and any(a in args for a in ("namespace", "ns", "all", "--all", "pv", "pvc", "node")):
        yield Ritual(
            "kubectl delete", "dire",
            "You would raze a whole village of containers, and their pods shall find no consecrated ground.",
            "Deletes Kubernetes resources in bulk (possibly a whole namespace and everything in it).",
            line,
        )

    elif prog == "docker" and "prune" in args:
        yield Ritual(
            "docker prune", "grave" if not ("-a" in args or "--all" in args or "--volumes" in args) else "dire",
            "A plague cart rolls through the container yard, collecting the dead and the merely sleeping alike.",
            "Removes stopped containers, unused images, networks and possibly volumes.",
            line,
        )

    elif prog == "terraform" and ("destroy" in args or "-destroy" in args):
        yield Ritual(
            "terraform destroy", "dire",
            "You read aloud the rite of unmaking over an entire estate of servers.",
            "Destroys all infrastructure managed by this Terraform state.",
            line,
        )

    elif prog == "dropdb":
        yield Ritual(
            "dropdb", "dire",
            "An entire library put to the torch, and every ledger with it.",
            "Deletes a whole PostgreSQL database.",
            line,
        )

    elif prog == "redis-cli" and any(a.lower() in ("flushall", "flushdb") for a in args):
        yield Ritual(
            "redis FLUSHALL", "dire",
            "You would wipe the memory of the house clean, as though no one had ever lived here.",
            "Deletes every key in the Redis database(s).",
            line,
        )


_SQL_RULES = [
    (re.compile(r"\bdrop\s+(database|schema)\b", re.I), "DROP DATABASE", "apocalyptic",
     "You would burn the entire archive, and every soul recorded within it.",
     "Deletes the whole database and all of its tables."),
    (re.compile(r"\bdrop\s+table\b", re.I), "DROP TABLE", "dire",
     "A whole family tree struck from the parish register.",
     "Deletes the table and all of its rows."),
    (re.compile(r"\btruncate\s+(table\s+)?[\w.\"`]+", re.I), "TRUNCATE", "dire",
     "Every name in the ledger blotted out in a single stroke of ink.",
     "Deletes every row in the table, usually with no way to roll back."),
    (re.compile(r"\bdelete\s+from\s+[\w.\"`]+\s*(;|\"|'|$)(?![^;]*\bwhere\b)", re.I), "DELETE without WHERE", "dire",
     "You condemn every row, guilty and innocent alike, for you have named no WHERE.",
     "Deletes every row in the table because there is no WHERE clause."),
    (re.compile(r"\.dropDatabase\s*\(", re.I), "dropDatabase()", "apocalyptic",
     "The whole Mongo crypt, sealed and forgotten.",
     "Deletes the whole MongoDB database."),
]

_LINE_RULES = [
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb", "apocalyptic",
     "A ghost that begets ghosts that beget ghosts, until the house itself chokes on the dead.",
     "Spawns processes endlessly until the machine freezes."),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"), "curl | sh", "grave",
     "You invite a stranger's spirit into your parlour without first asking its name.",
     "Downloads a script and runs it immediately without letting you read it."),
    (re.compile(r">\s*/dev/(sd[a-z]|nvme\d|disk\d|hd[a-z])"), "redirect onto a raw disk", "apocalyptic",
     "You pour ink over the very bones of the disk.",
     "Overwrites a raw block device, destroying its partition table and data."),
]


def analyze(line: str) -> Optional[Ritual]:
    """Return the most severe forbidden ritual in a command line, or None if it is harmless."""
    if not line or not line.strip():
        return None
    found: List[Ritual] = []
    for argv in split_commands(line):
        if argv:
            found.extend(_check_simple(argv, line))
    for pattern, name, severity, lore, consequence in _SQL_RULES + _LINE_RULES:
        if pattern.search(line):
            found.append(Ritual(name, severity, lore, consequence, line))
    if not found:
        return None
    return max(found, key=lambda r: r.rank)
