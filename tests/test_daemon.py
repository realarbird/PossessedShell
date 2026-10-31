import argparse
import asyncio
import json
import os

from possessed_shell import cli, client
from possessed_shell.daemon import Daemon
from possessed_shell.models import Failure
from fakes import FakeBrain, FakeMemory


async def _with_daemon(settings, scenario, brain=None):
    settings.omens = False
    daemon = Daemon(settings, brain=brain or FakeBrain(), memory=FakeMemory())
    server = asyncio.ensure_future(daemon.serve(warm=False))
    for _ in range(100):
        if settings.socket_path.exists():
            break
        await asyncio.sleep(0.01)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, scenario)
    finally:
        daemon.stop()
        await server


def test_round_trip_over_the_socket(settings):
    def scenario():
        ping = client.request(settings, {"type": "ping"}, 2)
        cursed = client.request(settings, {"type": "curse", "failure": Failure("ls /nope", 2, stderr="nope").to_dict()}, 5)
        skipped = client.request(settings, {"type": "curse", "failure": Failure("grep x y", 1).to_dict()}, 5)
        ritual = client.request(settings, {"type": "ritual", "command": "rm -rf /", "name": "rm -rf",
                                           "severity": "apocalyptic", "decision": "banished"}, 2)
        bogus = client.request(settings, {"type": "seance"}, 2)
        return ping, cursed, skipped, ritual, bogus

    ping, cursed, skipped, ritual, bogus = asyncio.run(_with_daemon(settings, scenario))
    assert ping["ok"] and ping["pid"] == os.getpid() and ping["model"] == "gemma2:2b"
    assert cursed["ok"] and cursed["haunting"]["lament"] == "Alas, poor process."
    assert cursed["haunting"]["fix"] == "ls -la /"
    assert skipped == {"ok": True, "skip": True}
    assert ritual == {"ok": True}
    assert bogus["ok"] is False
    assert not settings.socket_path.exists(), "socket should be removed on shutdown"


def test_client_curse_uses_the_daemon(settings):
    haunting = asyncio.run(_with_daemon(settings, lambda: client.curse(settings, Failure("ls /nope", 2))))
    assert haunting.source == "gemma2:2b"


def test_client_falls_back_in_process_when_no_daemon(settings, monkeypatch):
    settings.llm_url = "http://127.0.0.1:9"  # nothing listens here: Gemma is silent too
    started = []
    monkeypatch.setattr(client, "start_daemon", lambda s: started.append(True))
    haunting = client.curse(settings, Failure("gti status", 127, stderr="bash: gti: command not found"))
    assert haunting.source == "remedy" and haunting.fix == "git status"
    assert started == [True]


def test_omens_reach_live_sessions_and_bury_dead_ones(settings):
    live = settings.session_dir(os.getpid())
    dead = settings.session_dir(999_999_999)
    live.mkdir(parents=True)
    dead.mkdir(parents=True)
    (dead / "stderr.log").write_text("old")
    daemon = Daemon(settings, brain=FakeBrain(), memory=FakeMemory())
    assert daemon.deliver_omen({"kind": "cpu", "title": "fevered processors"}) == 1
    assert json.loads((live / "omen.json").read_text())["kind"] == "cpu"
    assert not dead.exists()


def test_daemon_pid_rejects_unrelated_process(settings, monkeypatch):
    settings.ensure_home()
    settings.pid_path.write_text("424242")

    class FakeProc:
        def status(self):
            return "running"

        def cmdline(self):
            return ["/usr/sbin/sshd", "-D"]

        def name(self):
            return "sshd"

    monkeypatch.setattr(client.psutil, "Process", lambda pid: FakeProc())
    assert client.daemon_pid(settings) is None


def test_corrupted_omen_file_is_unlinked_cleanly(settings):
    session = settings.session_dir(12345)
    session.mkdir(parents=True)
    omen_file = session / "omen.json"
    omen_file.write_text("{corrupted-json")
    rc = cli.cmd_omen(argparse.Namespace(demo=False, session="12345"), settings)
    assert rc == 0
    assert not omen_file.exists()
