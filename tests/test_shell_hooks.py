"""End-to-end: a real interactive shell in a pty with the hook loaded (Gemma offline -> grimoire)."""

import json
import re
import shutil
import subprocess

import pytest

from possessed_shell.cli import hook_script
from shellpty import PROMPT, ShellPty, hooked_env


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_hook_scripts_parse(shell, tmp_path):
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    script = tmp_path / f"hook.{shell}"
    script.write_text(hook_script(shell))
    result = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def start_shell(shell, tmp_path):
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    hook = tmp_path / f"hook.{shell}"
    hook.write_text(hook_script(shell))
    env = hooked_env(tmp_path, shell)
    if shell == "bash":
        rc = tmp_path / ".bashrc"
        rc.write_text(f"PS1='{PROMPT}'\nsource {hook}\n")
        argv = ["bash", "--noprofile", "--rcfile", str(rc), "-i"]
    else:
        (tmp_path / ".zshrc").write_text(f"PS1='{PROMPT}'\nunsetopt PROMPT_SP\nsource {hook}\n")
        env["ZDOTDIR"] = str(tmp_path)
        argv = ["zsh", "-i"]
    sh = ShellPty(argv, env, tmp_path)
    sh.expect(PROMPT)
    return sh


@pytest.fixture(params=["bash", "zsh"])
def shell(request, tmp_path):
    sh = start_shell(request.param, tmp_path)
    sh.name = request.param
    yield sh
    sh.close()


def run(sh, line, timeout=60):
    sh.sendline(line)
    return sh.expect(PROMPT, timeout)


def test_failed_command_is_cursed_with_its_captured_stderr(shell):
    out = run(shell, "ls /crypt/of/nothing")
    assert "HAS DIED" in out
    assert re.search(r"exit [12]\b", out)  # GNU ls exits 2, BSD/macOS ls exits 1
    # The diagnosis comes from the captured stderr ("No such file or directory").
    assert "`/crypt/of/nothing` does not exist" in out
    assert "ls -la /" in out
    if shell.name == "bash":
        assert "ls -la /" in run(shell, "history 2")  # Up-arrow summons the remedy
    else:
        # zsh pre-types the remedy on the next prompt; Ctrl-U refuses it.
        shell.expect(re.escape("ls -la /"), 5)
        shell.send("\x15")


def test_benign_failures_and_successes_are_left_in_peace(shell):
    out = run(shell, "grep zzz-not-there /etc/hosts")
    assert "HAS DIED" not in out
    out = run(shell, "echo still-alive")
    assert "still-alive" in out and "HAS DIED" not in out


def test_forbidden_ritual_can_be_banished_and_summoned(shell, tmp_path):
    grave = tmp_path / "graveyard"
    marker = tmp_path / "marker"
    grave.mkdir()

    shell.sendline(f"echo before && rm -rf {grave} && touch {marker}")
    shell.expect(r"FORBIDDEN RITUAL: RM -RF")
    shell.expect(re.escape("[y/N]"))
    shell.sendline("n")
    out = shell.expect("circle is broken")
    if shell.name == "zsh":
        # zsh redraws the banished line, unrun, for editing; once it is back in the editor, clear it.
        out += shell.expect("/marker")
        shell.send("\x15")
    out += run(shell, "echo sync-point")
    assert grave.exists() and not marker.exists()
    assert not re.search(r"^before$", out, re.M), "nothing on a banished line may run"
    assert "HAS DIED" not in out, "a banished command must not be cursed"

    shell.sendline(f"rm -rf {grave}")
    shell.expect(re.escape("[y/N]"))
    shell.sendline("y")
    out = shell.expect(PROMPT)
    assert "ritual begins" in out
    assert not grave.exists()


def test_omens_appear_at_the_next_prompt(shell, tmp_path):
    session = tmp_path / ".possessed" / "sessions" / str(shell.pid)
    assert session.is_dir()
    (session / "omen.json").write_text(json.dumps({
        "kind": "memory", "title": "drowning memory", "message": "Ectoplasm seeps from the memory banks!",
        "detail": "RAM at 95%", "fix": "free -h"}))
    out = run(shell, "true")
    assert "ECTOPLASM LEAK: DROWNING MEMORY" in out
    assert not (session / "omen.json").exists()


def test_bash_chains_existing_multiline_traps_cleanly(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash not installed")
    hook = tmp_path / "hook.bash"
    hook.write_text(hook_script("bash"))
    marker = tmp_path / "prior_trap_ran"
    rc = tmp_path / ".bashrc"
    rc.write_text(
        "trap 'if true; then\n  :;\nfi;' DEBUG\n"
        f"trap 'touch {marker}; ' EXIT\n"
        f"PS1='{PROMPT}'\n"
        f"source {hook}\n"
    )
    env = hooked_env(tmp_path, "bash")
    sh = ShellPty(["bash", "--noprofile", "--rcfile", str(rc), "-i"], env, tmp_path)
    try:
        sh.expect(PROMPT)
        out = run(sh, "echo trap-ok")
        assert "trap-ok" in out
        assert "syntax error" not in out
        sh.sendline("exit")
        for _ in range(50):
            if marker.exists():
                break
            import time
            time.sleep(0.05)
        assert marker.exists()
    finally:
        sh.close()
