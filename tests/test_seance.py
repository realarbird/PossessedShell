import asyncio

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from possessed_shell import telemetry
from possessed_shell.models import Failure
from possessed_shell.seance import perform_curse, should_haunt
from fakes import FakeBrain, FakeMemory

STATE = {"cpu_percent": 12.0, "memory_percent": 40.0, "top_process": {"name": "python", "pid": 1, "memory_percent": 2.0}}


def curse(settings, failure, brain, memory=None):
    return asyncio.run(perform_curse(failure, settings=settings, brain=brain, memory=memory or FakeMemory(),
                                     snapshot=lambda cwd: STATE))


def test_should_haunt(settings):
    assert should_haunt(Failure("ls /nope", 2), settings)
    assert not should_haunt(Failure("grep foo bar.txt", 1), settings)  # "no match" is an answer
    assert should_haunt(Failure("grep foo missing.txt", 2), settings)
    assert not should_haunt(Failure("sleep 100", 130), settings)  # Ctrl-C
    assert not should_haunt(Failure("git diff --exit-code", 1), settings)  # "there are differences"
    assert not should_haunt(Failure("git diff --quiet HEAD~1", 1), settings)
    assert not should_haunt(Failure("git -C /tmp diff --quiet", 1), settings)
    assert not should_haunt(Failure("git --no-pager diff --exit-code", 1), settings)
    assert not should_haunt(Failure("git grep needle", 1), settings)
    assert should_haunt(Failure("git grep --bogus-flag needle", 129), settings)
    assert should_haunt(Failure("git diff --exit-code nope..", 128), settings)
    assert should_haunt(Failure("git push", 1), settings)
    assert not should_haunt(Failure("ls", 0), settings)
    settings.disabled = True
    assert not should_haunt(Failure("ls /nope", 2), settings)


def test_gemma_answer_becomes_a_haunting(settings):
    brain = FakeBrain(usage={"input_tokens": 500, "output_tokens": 212})
    memory = FakeMemory(sins=["2026-10-01: `ls /nope` died with exit 2"])
    failure = Failure("ls /nope", 2, stderr="ls: cannot access '/nope': No such file or directory", cwd="/tmp", shell="zsh")
    haunting = curse(settings, failure, brain, memory)
    assert haunting.source == "gemma2:2b"
    assert haunting.fix == "ls -la /"
    assert haunting.past_sins == memory.sins
    assert haunting.command == "ls /nope" and haunting.exit_code == 2
    assert haunting.tokens == 212 and haunting.latency >= 0
    prompt = brain.prompts[0]
    assert "COMMAND: ls /nope" in prompt and "No such file or directory" in prompt and "PAST SINS" in prompt
    assert memory.inscribed and memory.inscribed[0][1] is haunting


def test_silent_gemma_falls_back_to_the_grimoire(settings):
    failure = Failure("gti status", 127, stderr="bash: gti: command not found")
    haunting = curse(settings, failure, FakeBrain(error="connection refused"))
    assert haunting.source == "remedy"
    assert haunting.fix == "git status"
    assert haunting.tokens == 0 and haunting.latency == 0


def test_useless_or_dangerous_fixes(settings):
    rerun = FakeBrain(reply={"lament": "Alas.", "diagnosis": "Typo.", "fix": "gti status"})
    assert curse(settings, Failure("gti status", 127, stderr="gti: command not found"), rerun).fix == "git status"

    reckless = FakeBrain(reply={"lament": "Alas.", "diagnosis": "Stale build.", "fix": "rm -rf build"})
    haunting = curse(settings, Failure("make", 2, stderr="make: *** [all] Error 1"), reckless)
    assert haunting.fix == "rm -rf build"
    assert "forbidden ritual" in haunting.fix_warning


class CapturingTransport(Transport):
    def __init__(self):
        super().__init__({})
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


def sent_spans(transport):
    """Every span sent, by op. Newer SDKs stream gen_ai.* spans as their own v2 `span` items."""
    spans = {}
    for env in transport.envelopes:
        for item in env.items:
            if item.type == "transaction":
                for sp in item.payload.json["spans"]:
                    spans[sp["op"]] = {"name": sp.get("description"), "span_id": sp["span_id"],
                                       "parent_span_id": sp.get("parent_span_id"),
                                       "failed": sp.get("status") not in (None, "ok"), "data": sp.get("data", {})}
            elif item.type == "span":
                for sp in item.payload.json["items"]:
                    attrs = {k: v["value"] for k, v in sp.get("attributes", {}).items()}
                    spans[attrs["sentry.op"]] = {"name": sp.get("name"), "span_id": sp["span_id"],
                                                 "parent_span_id": sp.get("parent_span_id"),
                                                 "failed": sp.get("status") == "error", "data": attrs}
    return spans


@pytest.fixture
def sentry(settings):
    transport = CapturingTransport()
    telemetry.init(settings, "test", dsn="https://public@sentry.example.com/1", transport=transport)
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.init()
    telemetry._enabled = False


def test_failed_command_is_traced_to_sentry(settings, sentry):
    failure = Failure("deploy --token sk_live_abcdefghijklmnopqrstuv", 1,
                      stderr="TOKEN=supersecret\nError: deploy failed", shell="bash", cwd="/srv")
    usage = {"input_tokens": 480, "output_tokens": 96, "load_seconds": 0.2, "tokens_per_second": 41.5,
             "response_model": "gemma2:2b"}
    haunting = curse(settings, failure, FakeBrain(usage=usage))
    sentry_sdk.flush()

    items = [item for env in sentry.envelopes for item in env.items]
    events = [i.payload.json for i in items if i.type == "event"]
    transactions = [i.payload.json for i in items if i.type == "transaction"]
    attachments = [i for i in items if i.type == "attachment"]

    assert len(events) == 1
    event = events[0]
    assert haunting.sentry_event_id == event["event_id"]
    assert event["exception"]["values"][0]["type"] == "ShellCommandFailed"
    assert event["tags"]["exit_code"] == "1" and event["tags"]["command.name"] == "deploy"
    assert event["contexts"]["system_state"]["cpu_percent"] == 12.0
    assert event["fingerprint"] == ["shell-command-failed", "deploy", "1"]
    assert "supersecret" not in str(event) and "sk_live" not in str(event)

    assert attachments and b"supersecret" not in attachments[0].payload.bytes
    assert b"deploy failed" in attachments[0].payload.bytes

    assert len(transactions) == 1
    assert transactions[0]["contexts"]["trace"]["op"] == "shell.curse"
    spans = sent_spans(sentry)
    assert {"ghost.snapshot", "ghost.memory.recall", "gen_ai.invoke_agent", "gen_ai.chat"} <= set(spans)
    assert "supersecret" not in str(spans) and "sk_live" not in str(spans)

    # Sentry AI agent tracing: the ghost's run, and the Gemma call inside it with its tokens and speed.
    agent, chat = spans["gen_ai.invoke_agent"], spans["gen_ai.chat"]
    assert chat["parent_span_id"] == agent["span_id"]
    assert agent["name"] == "invoke_agent Cornelius Vale"
    assert agent["data"]["gen_ai.agent.name"] == "Cornelius Vale" and agent["data"]["outcome"] == "gemma"
    assert not chat["failed"]
    data = chat["data"]
    assert chat["name"] == "chat gemma2:2b"
    assert data["gen_ai.operation.name"] == "chat" and data["gen_ai.system"] == "ollama"
    assert data["gen_ai.request.model"] == "gemma2:2b" and data["gen_ai.response.model"] == "gemma2:2b"
    assert (data["gen_ai.usage.input_tokens"], data["gen_ai.usage.output_tokens"], data["gen_ai.usage.total_tokens"]) == (480, 96, 576)
    assert data["ollama.tokens_per_second"] == 41.5 and data["ollama.load_seconds"] == 0.2
    assert "deploy failed" in data["gen_ai.request.messages"]
    assert "Alas, poor process." in data["gen_ai.response.text"]


def test_silent_gemma_marks_the_chat_span_failed(settings, sentry):
    curse(settings, Failure("gti status", 127, stderr="gti: command not found"), FakeBrain(error="connection refused"))
    sentry_sdk.flush()
    spans = sent_spans(sentry)
    assert spans["gen_ai.chat"]["failed"]
    assert spans["gen_ai.invoke_agent"]["data"]["outcome"] == "remedy"
