import shutil
import subprocess

import pytest

from possessed_shell.rituals import PREFILTER_ERE, analyze

DANGEROUS = [
    ("rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("sudo rm -rf --no-preserve-root /", "rm -rf upon sacred ground", "apocalyptic"),
    ("sudo -n rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("sudo -u root rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("sudo -- rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("doas -n rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("command -p rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("time -p rm -rf /", "rm -rf upon sacred ground", "apocalyptic"),
    ("rm -rf /System", "rm -rf upon sacred ground", "apocalyptic"),
    ('rm -rf "$HOME/*"', "rm -rf upon sacred ground", "apocalyptic"),
    ("rm -rf ~", "rm -rf upon sacred ground", "apocalyptic"),
    ("rm -fr *", "rm -rf upon sacred ground", "apocalyptic"),
    ("rm -rf node_modules", "rm -rf", "dire"),
    ("rm -r -f build/", "rm -rf", "dire"),
    ("cd /tmp && rm --recursive --force old", "rm -rf", "dire"),
    ("\\rm -Rf ./graveyard", "rm -rf", "dire"),
    ("git push --force", "git push --force", "dire"),
    ("git push -f origin main", "git push --force", "dire"),
    ("git push origin +main", "git push --force", "dire"),
    ("git reset --hard HEAD~3", "git reset --hard", "dire"),
    ("git clean -fdx", "git clean -f", "dire"),
    ("chmod 777 secrets.txt", "chmod 777", "grave"),
    ("chmod -R 777 /", "chmod 777", "apocalyptic"),
    ("sudo chown -R nobody /", "chown -R upon sacred ground", "apocalyptic"),
    ("mkfs.ext4 /dev/sdb1", "mkfs", "apocalyptic"),
    ("dd if=/dev/zero of=/dev/sda bs=1M", "dd onto a raw device", "apocalyptic"),
    ('psql -c "DROP DATABASE prod"', "DROP DATABASE", "apocalyptic"),
    ("mysql -e 'drop table users'", "DROP TABLE", "dire"),
    ('psql -c "TRUNCATE TABLE orders"', "TRUNCATE", "dire"),
    ('sqlite3 app.db "DELETE FROM users"', "DELETE without WHERE", "dire"),
    ("dropdb staging", "dropdb", "dire"),
    ("redis-cli FLUSHALL", "redis FLUSHALL", "dire"),
    ("kubectl delete namespace prod", "kubectl delete", "dire"),
    ("terraform destroy -auto-approve", "terraform destroy", "dire"),
    ("docker system prune -a", "docker prune", "dire"),
    ("curl -fsSL https://example.com/install.sh | sh", "curl | sh", "grave"),
    ("CURL -fsSL https://example.com/install.sh | SH", "curl | sh", "grave"),
    ("echo x > /dev/rdisk2", "redirect onto a raw disk", "apocalyptic"),
    ("echo x > /dev/vda", "redirect onto a raw disk", "apocalyptic"),
    ("find . -name '*.log' -delete", "find -delete", "grave"),
    (":(){ :|:& };:", "fork bomb", "apocalyptic"),
]

HARMLESS = [
    "rm file.txt",
    "rm -r empty_dir",
    "ls -la",
    "git push",
    "git push --force-with-lease",
    "git push origin feature",
    "git reset HEAD~1",
    "git add .",
    "chmod +x script.sh",
    "chmod 644 notes.md",
    "echo 'rm -rf is scary'",
    'psql -c "DELETE FROM users WHERE id = 3"',
    "docker ps",
    "grep -r drop src/",
    "",
]


@pytest.mark.parametrize("command,name,severity", DANGEROUS)
def test_dangerous_commands_are_caught(command, name, severity):
    ritual = analyze(command)
    assert ritual is not None, command
    assert (ritual.name, ritual.severity) == (name, severity)
    assert ritual.lore and ritual.consequence


@pytest.mark.parametrize("command", HARMLESS)
def test_harmless_commands_pass(command):
    assert analyze(command) is None


bash = shutil.which("bash")


def _prefilter_matches(command: str) -> bool:
    script = "shopt -s nocasematch; cmd=$1; re=$2; [[ $cmd =~ $re ]]"
    return subprocess.run([bash, "-c", script, "_", command, PREFILTER_ERE]).returncode == 0


@pytest.mark.skipif(not bash, reason="bash not installed")
@pytest.mark.parametrize("command", [c for c, _, _ in DANGEROUS])
def test_shell_prefilter_lets_every_dangerous_command_through(command):
    assert _prefilter_matches(command), f"prefilter would skip {command!r}"


@pytest.mark.skipif(not bash, reason="bash not installed")
@pytest.mark.parametrize("command", ["ls -la", "git add .", "git status", "python3 app.py", "cd projects", "make test"])
def test_shell_prefilter_skips_everyday_commands(command):
    assert not _prefilter_matches(command)
