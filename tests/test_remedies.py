import os

from possessed_shell import remedies
from possessed_shell.models import Failure, command_name


def diagnose(command, code, stderr, cwd=""):
    return remedies.diagnose(Failure(command=command, exit_code=code, stderr=stderr, cwd=cwd))


def test_command_name_skips_wrappers():
    assert command_name("sudo -E FOO=1 python3 x.py | tee log") == "python3"
    assert command_name("FOO=bar /usr/bin/git status") == "git"


def test_typo_in_command_name():
    _, fix = diagnose("gti status", 127, "bash: gti: command not found")
    assert fix == "git status"
    _, fix = diagnose("sl -la", 127, "zsh: command not found: sl")
    assert fix == "ls -la"


def test_missing_program_suggests_install(monkeypatch):
    monkeypatch.setattr(remedies, "_executables_on_path", lambda: [])
    diagnosis, fix = diagnose("cowsay boo", 127, "bash: cowsay: command not found")
    assert "cowsay" in diagnosis
    assert fix.endswith("cowsay")


def test_python_module_maps_to_pip_name():
    _, fix = diagnose("python3 app.py", 1, "Traceback...\nModuleNotFoundError: No module named 'yaml'")
    assert fix.endswith("-m pip install pyyaml")


def test_git_upstream_hint_is_lifted_from_stderr():
    stderr = ("fatal: The current branch feat has no upstream branch.\n"
              "To push the current branch and set the remote as upstream, use\n\n"
              "    git push --set-upstream origin feat\n")
    assert diagnose("git push", 128, stderr)[1] == "git push --set-upstream origin feat"


def test_git_similar_command():
    stderr = "git: 'stauts' is not a git command. See 'git --help'.\n\nThe most similar command is\n\tstatus\n"
    assert diagnose("git stauts", 1, stderr)[1] == "git status"


def test_misspelt_path_finds_its_sibling(tmp_path):
    (tmp_path / "Documents").mkdir()
    _, fix = diagnose("cd Documnets", 1, "bash: cd: Documnets: No such file or directory", cwd=str(tmp_path))
    assert fix == "cd Documents"
    _, fix = diagnose("ls /crypt/of/nothing", 2, "ls: cannot access '/crypt/of/nothing': No such file or directory")
    assert fix == "ls -la /"


def test_script_not_executable(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text("echo hi")
    cmd = str(script)
    _, fix = diagnose(cmd, 126, f"bash: {cmd}: Permission denied")
    assert fix == f"chmod +x {cmd}"


def test_port_in_use():
    _, fix = diagnose("npm start", 1, "Error: listen EADDRINUSE: address already in use :::3000")
    assert fix == "lsof -i :3000"


def test_unknown_failure_has_no_fix_but_quotes_the_error():
    diagnosis, fix = diagnose("./weird", 3, "something odd happened\nreally odd\n")
    assert fix == ""
    assert "really odd" in diagnosis


def test_remedy_mourns_repeat_sins():
    haunting = remedies.remedy(Failure(command="gti status", exit_code=127, stderr="gti: command not found"), ["before"])
    assert haunting.source == "remedy"
    assert haunting.past_sins == ["before"]
    assert "gti" in haunting.lament
