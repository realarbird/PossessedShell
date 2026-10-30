import asyncio
import json

import httpx
import pytest

from possessed_shell.brain import Brain, BrainError, clean_fix, parse_reply
from possessed_shell.prompts import GHOST_SYSTEM_PROMPT, HAUNTING_SCHEMA

REPLY = {"lament": "Alas, poor ls.", "diagnosis": "The path does not exist.", "fix": "ls -la /"}


def test_parse_reply_plain_and_fenced():
    assert parse_reply(json.dumps(REPLY)) == REPLY
    assert parse_reply("```json\n" + json.dumps(REPLY) + "\n```") == REPLY
    assert parse_reply("Here you go: " + json.dumps(REPLY) + " Farewell.") == REPLY


def test_parse_reply_rejects_garbage():
    with pytest.raises(BrainError):
        parse_reply("I am but a humble ghost")
    with pytest.raises(BrainError):
        parse_reply(json.dumps({"lament": "", "diagnosis": "x", "fix": "y"}))


@pytest.mark.parametrize("raw,clean", [
    ("`ls -la`", "ls -la"),
    ("$ pip install x", "pip install x"),
    ("```bash\ngit pull --rebase\n```", "git pull --rebase"),
    ("# first\nmake build\nmake test", "make build"),
    ("none", ""),
    ("", ""),
])
def test_clean_fix(raw, clean):
    assert clean_fix(raw) == clean


def _brain(settings, handler):
    return Brain(settings, transport=httpx.MockTransport(handler))


def test_ollama_request_shape(settings):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "gemma2:2b", "message": {"role": "assistant", "content": json.dumps(REPLY)}, "done": True,
            "total_duration": 2_400_000_000, "load_duration": 300_000_000,
            "prompt_eval_count": 512, "prompt_eval_duration": 900_000_000,
            "eval_count": 120, "eval_duration": 1_500_000_000,
        })

    result = asyncio.run(_brain(settings, handler).haunt("COMMAND: ls"))
    usage = result.pop("usage")
    assert result == REPLY
    assert usage == {"input_tokens": 512, "output_tokens": 120, "tokens_per_second": 80.0, "load_seconds": 0.3,
                     "response_model": "gemma2:2b"}
    body = seen["body"]
    assert seen["path"] == "/api/chat"
    assert body["model"] == "gemma2:2b"
    assert body["format"] == HAUNTING_SCHEMA
    assert body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": GHOST_SYSTEM_PROMPT}
    assert body["messages"][1]["content"] == "COMMAND: ls"


def test_openai_compatible_server(settings):
    settings.llm_api = "openai"

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"model": "gemma-2-2b-it", "choices": [{"message": {"content": json.dumps(REPLY)}}],
                                         "usage": {"prompt_tokens": 300, "completion_tokens": 90, "total_tokens": 390}})

    result = asyncio.run(_brain(settings, handler).haunt("x"))
    assert result.pop("usage") == {"input_tokens": 300, "output_tokens": 90, "response_model": "gemma-2-2b-it"}
    assert result == REPLY


def test_missing_usage_is_left_out_not_guessed(settings):
    reply = {"message": {"content": json.dumps(REPLY)}}
    result = asyncio.run(_brain(settings, lambda r: httpx.Response(200, json=reply)).haunt("x"))
    assert result["usage"] == {}


def test_server_errors_become_brain_errors(settings):
    with pytest.raises(BrainError):
        asyncio.run(_brain(settings, lambda r: httpx.Response(500, text="model exploded")).haunt("x"))

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(BrainError):
        asyncio.run(_brain(settings, refuse).haunt("x"))


def test_status_reports_missing_model(settings):
    brain = _brain(settings, lambda r: httpx.Response(200, json={"models": [{"name": "llama3:8b"}]}))
    ok, msg = asyncio.run(brain.status())
    assert not ok and "ollama pull gemma2:2b" in msg
    brain = _brain(settings, lambda r: httpx.Response(200, json={"models": [{"name": "gemma2:2b"}]}))
    assert asyncio.run(brain.status())[0]
