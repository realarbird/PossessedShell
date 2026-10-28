import io

import pytest
from rich.console import Console

from possessed_shell import render
from possessed_shell.models import Haunting
from possessed_shell.rituals import analyze


@pytest.fixture
def console(monkeypatch):
    monkeypatch.setattr(render.time, "sleep", lambda s: None)
    c = Console(file=io.StringIO(), force_terminal=True, width=90, color_system="truecolor")
    c.animate = True
    c.cps = 10_000
    return c


def text(console):
    return console.file.getvalue()


def test_animated_haunting(console):
    haunting = Haunting(lament="Alas, poor ls. " * 8, diagnosis="The path does not exist.", fix="ls -la /",
                        source="gemma2:2b", past_sins=["once"], sentry_event_id="abc123def4567890",
                        command="ls /nope", exit_code=2, fix_warning="careful")
    with render.summoning(console):
        pass
    render.render_haunting(console, haunting, shell="zsh")
    out = text(console)
    assert "HAS DIED" in out and "ls -la /" in out and "1 past sin" in out and "sentry abc123def456" in out
    assert "\x1b[38;2;" in out  # blood red and ghostly cyan, in true colour


def test_animated_ritual_and_omen(console):
    render.render_ritual(console, analyze("git push --force"))
    render.render_omen(console, {"title": "drowning memory", "message": "Ectoplasm!", "detail": "RAM 95%", "fix": "free -h"})
    out = text(console)
    assert "GIT PUSH --FORCE" in out and "ECTOPLASM LEAK" in out and "free -h" in out


def test_glitch_keeps_layout():
    art = "ab cd\nef gh"
    glitched = render.glitch(art, 1.0)
    assert glitched.count("\n") == 1 and glitched.count(" ") == 2
    assert render.glitch(art, 0.0) == art
