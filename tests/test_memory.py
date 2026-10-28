import asyncio
import json
import types
import uuid

from possessed_shell.memory import ASSISTANT_NAME, GhostMemory, summarize
from possessed_shell.models import Failure, Haunting


class FakeBackboard:
    """Mimics the parts of backboard.BackboardClient the ghost uses."""

    def __init__(self, existing=False):
        self.assistants = [types.SimpleNamespace(assistant_id=uuid.uuid4(), name=ASSISTANT_NAME)] if existing else []
        self.memories = []
        self.created = 0

    async def list_assistants(self, skip=0, limit=100, name=None):
        return [a for a in self.assistants if a.name == name][:limit]

    async def create_assistant(self, name, description=None, system_prompt=None, **kw):
        self.created += 1
        assistant = types.SimpleNamespace(assistant_id=uuid.uuid4(), name=name, system_prompt=system_prompt)
        self.assistants.append(assistant)
        return assistant

    async def add_memory(self, assistant_id, content, metadata=None):
        self.memories.append(types.SimpleNamespace(id=str(len(self.memories)), content=content, metadata=metadata))
        return {"success": True}

    async def get_memories(self, assistant_id, page=None, page_size=None):
        return types.SimpleNamespace(memories=list(self.memories), total_count=len(self.memories))


def failure(command, code, stderr, ts):
    return Failure(command=command, exit_code=code, stderr=stderr, cwd="/home/me/project", timestamp=ts)


def test_assistant_is_created_once_and_cached(settings):
    fake = FakeBackboard()
    memory = GhostMemory(settings, client=fake)
    first = asyncio.run(memory.assistant_id())
    assert fake.created == 1
    assert json.loads(settings.state_path.read_text())["backboard_assistant_id"] == first
    # A fresh process reads the cached id instead of creating another assistant.
    assert asyncio.run(GhostMemory(settings, client=fake).assistant_id()) == first
    assert fake.created == 1


def test_existing_assistant_is_reused(settings):
    fake = FakeBackboard(existing=True)
    assert asyncio.run(GhostMemory(settings, client=fake).assistant_id()) == str(fake.assistants[0].assistant_id)
    assert fake.created == 0


def test_inscribe_then_recall_same_program_newest_first(settings):
    fake = FakeBackboard()
    memory = GhostMemory(settings, client=fake)

    async def scenario():
        await memory.inscribe(failure("python3 a.py", 1, "No module named 'yaml'", 100), Haunting("l", "d", "pip install pyyaml"))
        await memory.inscribe(failure("ls /nope", 2, "No such file", 200), Haunting("l", "d"))
        await memory.inscribe(failure("python3 b.py", 1, "SyntaxError", 300), Haunting("l", "d"))
        return await memory.recall("python3")

    sins = asyncio.run(scenario())
    assert len(sins) == 2
    assert "b.py" in sins[0] and "a.py" in sins[1]
    assert fake.memories[0].metadata == {"kind": "haunting", "command_name": "python3", "exit_code": 1, "timestamp": 100}


def test_memory_scrubs_secrets_and_never_raises(settings):
    text = summarize(failure("deploy --token abcdef123456", 1, "API_KEY=sk_live_secret rejected", 0), Haunting("l", "d"))
    assert "abcdef123456" not in text and "sk_live_secret" not in text

    class Broken(FakeBackboard):
        async def get_memories(self, *a, **k):
            raise ConnectionError("the veil is closed")

        async def add_memory(self, *a, **k):
            raise ConnectionError("the veil is closed")

    memory = GhostMemory(settings, client=Broken())
    assert asyncio.run(memory.recall("ls")) == []
    assert asyncio.run(memory.inscribe(failure("ls", 2, "", 0), Haunting("l", "d"))) is False


def test_disabled_without_api_key(settings):
    memory = GhostMemory(settings)
    assert not memory.enabled
    assert asyncio.run(memory.recall("ls")) == []
