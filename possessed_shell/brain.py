"""The local brain: Gemma 2 running in Ollama (or any OpenAI-compatible local server).

Nothing here talks to the internet; the default URL is Ollama on localhost.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from possessed_shell.config import Settings
from possessed_shell.prompts import GHOST_SYSTEM_PROMPT, HAUNTING_SCHEMA, WARMUP_PROMPT

TEMPERATURE = 0.7
MAX_TOKENS = 400


class BrainError(Exception):
    """Gemma is unreachable, too slow, or answered with something we cannot use."""


def _extract_json(content: str) -> Dict:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise BrainError(f"no JSON in reply: {content[:120]!r}")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise BrainError(f"unparseable JSON in reply: {exc}") from exc


def clean_fix(fix: str) -> str:
    """Reduce whatever the model put in `fix` to a single runnable line, or ''."""
    if not fix:
        return ""
    fix = re.sub(r"^\s*```[\w-]*[ \t]*\n?|\n?```\s*$", "", fix.strip())
    fix = fix.strip().strip("`").strip()
    lines = [ln.strip() for ln in fix.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return ""
    line = re.sub(r"^\$\s+", "", lines[0]).strip("`").strip()
    if len(line) > 300 or line.lower() in ("none", "n/a", "null", '""'):
        return ""
    return line


def parse_reply(content: str) -> Dict[str, str]:
    data = _extract_json(content)
    if not isinstance(data, dict):
        raise BrainError("reply is not a JSON object")
    lament = str(data.get("lament") or "").strip()
    diagnosis = str(data.get("diagnosis") or "").strip()
    if not lament:
        raise BrainError("reply has no lament")
    return {"lament": lament, "diagnosis": diagnosis, "fix": clean_fix(str(data.get("fix") or ""))}


def _ollama_usage(data: Dict) -> Dict[str, Any]:
    """Token counts and timings from an Ollama /api/chat reply (durations there are in nanoseconds)."""
    usage: Dict[str, Any] = {}
    if isinstance(data.get("prompt_eval_count"), int):
        usage["input_tokens"] = data["prompt_eval_count"]
    if isinstance(data.get("eval_count"), int):
        usage["output_tokens"] = data["eval_count"]
        if data.get("eval_duration"):
            usage["tokens_per_second"] = round(data["eval_count"] / (data["eval_duration"] / 1e9), 1)
    if isinstance(data.get("load_duration"), int):
        usage["load_seconds"] = round(data["load_duration"] / 1e9, 3)
    if data.get("model"):
        usage["response_model"] = data["model"]
    return usage


def _openai_usage(data: Dict) -> Dict[str, Any]:
    tokens = data.get("usage") or {}
    usage: Dict[str, Any] = {}
    if isinstance(tokens.get("prompt_tokens"), int):
        usage["input_tokens"] = tokens["prompt_tokens"]
    if isinstance(tokens.get("completion_tokens"), int):
        usage["output_tokens"] = tokens["completion_tokens"]
    if data.get("model"):
        usage["response_model"] = data["model"]
    return usage


class Brain:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.settings = settings
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.settings.llm_url.rstrip("/"), timeout=timeout, transport=self._transport)

    def _messages(self, prompt: str) -> List[Dict[str, str]]:
        return [{"role": "system", "content": GHOST_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

    async def _chat(self, messages: List[Dict[str, str]], timeout: float, structured: bool) -> Tuple[str, Dict[str, Any]]:
        """(reply text, usage): usage holds whatever token counts and timings the server reported."""
        s = self.settings
        try:
            async with self._client(timeout) as client:
                if s.llm_api == "openai":
                    body: Dict = {"model": s.model, "messages": messages, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS}
                    if structured:
                        body["response_format"] = {"type": "json_object"}
                    resp = await client.post("/v1/chat/completions", json=body)
                    if resp.status_code == 400 and structured:  # some servers reject response_format
                        body.pop("response_format")
                        resp = await client.post("/v1/chat/completions", json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"], _openai_usage(data)

                body = {
                    "model": s.model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"temperature": TEMPERATURE, "num_predict": MAX_TOKENS},
                }
                if structured:
                    body["format"] = HAUNTING_SCHEMA
                resp = await client.post("/api/chat", json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["message"]["content"], _ollama_usage(data)
        except httpx.TimeoutException as exc:
            raise BrainError(f"{s.model} took longer than {timeout:.0f}s") from exc
        except httpx.HTTPStatusError as exc:
            raise BrainError(f"{s.llm_url} answered {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise BrainError(f"cannot reach {s.llm_url}: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BrainError(f"unexpected reply shape from {s.llm_url}: {exc}") from exc

    async def haunt(self, prompt: str) -> Dict[str, Any]:
        """Ask Gemma for {lament, diagnosis, fix} about a dead command, plus the `usage` the server reported."""
        content, usage = await self._chat(self._messages(prompt), self.settings.llm_timeout, structured=True)
        return {**parse_reply(content), "usage": usage}

    async def warm_up(self) -> None:
        """Load the model into memory so the first real haunting is quick."""
        await self._chat([{"role": "user", "content": WARMUP_PROMPT}], max(self.settings.llm_timeout, 120.0), structured=False)

    async def status(self) -> Tuple[bool, str]:
        """(ok, message) describing whether the model server is up and the model is pulled."""
        s = self.settings
        try:
            async with self._client(5.0) as client:
                if s.llm_api == "openai":
                    resp = await client.get("/v1/models")
                    resp.raise_for_status()
                    names = [m.get("id", "") for m in resp.json().get("data", [])]
                else:
                    resp = await client.get("/api/tags")
                    resp.raise_for_status()
                    names = [m.get("name", "") for m in resp.json().get("models", [])]
        except httpx.HTTPError as exc:
            return False, f"cannot reach {s.llm_url} ({exc.__class__.__name__})"
        wanted = {s.model, f"{s.model}:latest"}
        if wanted & set(names):
            return True, f"{s.model} is ready at {s.llm_url}"
        hint = f"run: ollama pull {s.model}" if s.llm_api == "ollama" else "load the model in your server"
        return False, f"{s.model} not found at {s.llm_url} ({hint})"
