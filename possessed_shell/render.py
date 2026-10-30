"""Everything the living see: glitchy banners, typewriter prose and the summoning spinner."""

from __future__ import annotations

import random
import textwrap
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from rich.console import Console
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from possessed_shell import banners
from possessed_shell.models import Haunting
from possessed_shell.rituals import Ritual

# The palette of the dead.
BLOOD = "#c0001a"
DEEP_BLOOD = "#7a0010"
CYAN = "#7df9ff"
BONE = "#e8e2d0"
ASH = "#8a8a8a"
ECTO = "#9dff00"
CANDLE = "#ffb347"

SEVERITY_STYLE = {"grave": CANDLE, "dire": BLOOD, "apocalyptic": f"bold {BLOOD} on #1a0000"}

_GLITCH_CHARS = "▓▒░█▚▞▟▙#%&@$*!?¿§¤"
_ZALGO = [chr(c) for c in range(0x0300, 0x036F)]

SUMMONING_PHRASES = [
    "the veil thins",
    "the planchette begins to move",
    "Gemma stirs in her crypt",
    "consulting the spirits of /var/log",
    "a cold draught passes through the CPU",
    "the candles gutter",
]


def make_console(animate: bool = True, cps: float = 90.0) -> Console:
    console = Console(highlight=False, soft_wrap=False)
    console.animate = animate and console.is_terminal  # type: ignore[attr-defined]
    console.cps = cps  # type: ignore[attr-defined]
    return console


def _animate(console: Console) -> bool:
    return bool(getattr(console, "animate", False))


def glitch(text: str, intensity: float) -> str:
    """Corrupt a fraction of the visible characters of `text`."""
    out = []
    for ch in text:
        if ch in " \n" or random.random() > intensity:
            out.append(ch)
        elif random.random() < 0.5:
            out.append(random.choice(_GLITCH_CHARS))
        else:
            out.append(ch + random.choice(_ZALGO))
    return "".join(out)


def reveal(console: Console, art: str, style: str, frames: int = 7, delay: float = 0.06) -> None:
    """Print ASCII art that flickers in through the static before it settles."""
    art = art.strip("\n")
    if not _animate(console):
        console.print(Text(art, style=style))
        return
    with Live(Text(glitch(art, 0.9), style=style), console=console, refresh_per_second=30, transient=False) as live:
        for i in range(frames):
            intensity = max(0.0, 0.85 - i * (0.85 / frames))
            colour = random.choice([style, CYAN, DEEP_BLOOD]) if i < frames - 2 else style
            live.update(Text(glitch(art, intensity), style=colour))
            time.sleep(delay)
        live.update(Text(art, style=style))


def typewriter(console: Console, text: str, style: str = CYAN, indent: int = 2, cps: Optional[float] = None) -> None:
    """Spell the text out one character at a time, the way a spirit board would."""
    width = max(20, min(console.width, 100) - indent - 2)
    lines = []
    for para in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width=width) or [""])
    prefix = " " * indent
    if not _animate(console):
        for line in lines:
            console.print(Text(prefix + line, style=style))
        return
    cps = cps or getattr(console, "cps", 90.0)
    for line in lines:
        console.print(prefix, end="")
        for ch in line:
            console.print(Text(ch, style=style), end="")
            console.file.flush()
            pause = 1.0 / cps
            if ch in ".!?;":
                pause *= 6
            elif ch == ",":
                pause *= 3
            time.sleep(pause * random.uniform(0.5, 1.5))
        console.print()


class _Summoning:
    """A flickering status line shown while Gemma composes her reply."""

    def __init__(self) -> None:
        self.phrase = random.choice(SUMMONING_PHRASES)
        self.started = time.monotonic()

    def __rich__(self) -> Text:
        elapsed = time.monotonic() - self.started
        if int(elapsed) % 4 == 0 and elapsed - int(elapsed) < 0.1:
            self.phrase = random.choice(SUMMONING_PHRASES)
        dots = "." * (int(elapsed * 3) % 4)
        moon = "◐◓◑◒"[int(elapsed * 6) % 4]
        body = glitch(f"{self.phrase}{dots}", 0.12)
        return Text.assemble((f"  {moon} ", CYAN), (body, f"italic {ASH}"), (f"  {elapsed:4.1f}s", DEEP_BLOOD))


@contextmanager
def summoning(console: Console) -> Iterator[None]:
    if not _animate(console):
        console.print(Text("  ◐ the veil thins...", style=f"italic {ASH}"))
        yield
        return
    with Live(_Summoning(), console=console, refresh_per_second=12, transient=True):
        yield


def render_haunting(console: Console, haunting: Haunting, shell: str = "") -> None:
    console.print()
    reveal(console, banners.CURSE_GHOST, BLOOD)
    title = f"☠  THE PROCESS `{haunting.command[:60]}` HAS DIED  (exit {haunting.exit_code})  ☠"
    console.print(Text(title, style=f"bold {BLOOD}"))
    console.print()
    typewriter(console, haunting.lament, style=f"italic {CYAN}")
    console.print()
    console.print(Padding(Text.assemble(("⚰  ", BONE), ("Diagnosis: ", f"bold {BONE}"), (haunting.diagnosis, BONE)), (0, 0, 0, 2)))
    if haunting.fix:
        console.print()
        console.print(Panel(Text(haunting.fix, style=f"bold {BONE}"), title="[bold]the ghost's remedy[/]",
                            title_align="left", border_style=BLOOD, padding=(0, 2), expand=False))
        if haunting.fix_warning:
            console.print(Text(f"  ⚠  {haunting.fix_warning}", style=f"bold {CANDLE}"))
        hint = {
            "zsh": "The remedy waits on your next prompt. Enter to accept, Ctrl-U to refuse.",
            "bash": "Press ↑ to summon the remedy onto your prompt.",
        }.get(shell)
        if hint:
            console.print(Text(f"  {hint}", style=ASH))
    source = ("whispered by " + haunting.source if haunting.source != "remedy"
              else "read from the old grimoire (Gemma was silent)")
    if haunting.latency:
        source += f" in {haunting.latency:.1f}s"
    if haunting.tokens:
        source += f", {haunting.tokens} tokens"
    extras = []
    if haunting.past_sins:
        extras.append((f"{len(haunting.past_sins)} past sin(s) remembered", f"dim {CANDLE}"))
    if haunting.sentry_event_id:
        extras.append((f"sentry {haunting.sentry_event_id[:12]}", f"dim {ASH}"))
    # Wrap between the footer's pieces, never inside one.
    footer = Text.assemble(("  ", ""), (source, f"dim {ASH}"))
    for extra, style in extras:
        piece = Text.assemble(("  ·  ", f"dim {ASH}"), (extra, style))
        if footer.cell_len + piece.cell_len > console.width:
            console.print(footer)
            footer = Text.assemble(("  ", ""), (extra, style))
        else:
            footer.append_text(piece)
    console.print(footer)
    console.print()


def render_ritual(console: Console, ritual: Ritual) -> None:
    style = SEVERITY_STYLE[ritual.severity]
    console.print()
    reveal(console, banners.RITUAL_SKULL, BLOOD)
    console.print(Text(f"  ⛤  A FORBIDDEN RITUAL: {ritual.name.upper()}  [{ritual.severity}]", style=f"bold {style}"))
    console.print(Padding(Text(f"$ {ritual.command}", style=f"bold {BONE}"), (0, 0, 0, 5)))
    console.print()
    typewriter(console, ritual.lore, style=f"italic {CYAN}")
    console.print()
    console.print(Padding(Text.assemble(("In plain words: ", f"bold {BONE}"), (ritual.consequence, BONE)), (0, 0, 0, 2)))
    console.print()


def render_omen(console: Console, omen: dict) -> None:
    console.print()
    reveal(console, banners.ECTOPLASM_BLOB, ECTO)
    console.print(Text(f"  ~ AN ECTOPLASM LEAK: {omen.get('title', 'something stirs').upper()} ~", style=f"bold {ECTO}"))
    console.print()
    typewriter(console, omen.get("message", ""), style=f"italic {CYAN}")
    if omen.get("detail"):
        console.print(Padding(Text(omen["detail"], style=BONE), (0, 0, 0, 2)))
    if omen.get("fix"):
        console.print(Text.assemble(("  Perhaps: ", f"bold {BONE}"), (omen["fix"], f"bold {ECTO}")))
    console.print()


def say(console: Console, text: str, style: str = f"italic {ASH}") -> None:
    console.print(Text(f"  {text}", style=style))
