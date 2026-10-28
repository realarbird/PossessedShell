"""The local brain: Gemma 2 running in Ollama (or any OpenAI-compatible local server).

Nothing here talks to the internet; the default URL is Ollama on localhost.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

import httpx

from possessed_shell.config import Settings
from possessed_shell.prompts import GHOST_SYSTEM_PROMPT, HAUNTING_SCHEMA, WARMUP_PROMPT


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


class Brain:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.settings = settings
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.settings.llm_url.rstrip("/"), timeout=timeout, transport=self._transport)

    def _messages(self, prompt: str) -> List[Dict[str, str]]:
        return [{"role": "system", "content": GHOST_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

    async def _chat(self, messages: List[Dict[str, str]], timeout: float, structured: bool) -> str:
        s = self.settings
        try:
            async with self._client(timeout) as client:
                if s.llm_api == "openai":
                    body: Dict = {"model": s.model, "messages": messages, "temperature": 0.7, "max_tokens": 400}
                    if structured:
                        body["response_format"] = {"type": "json_object"}
                    resp = await client.post("/v1/chat/completions", json=body)
                    if resp.status_code == 400 and structured:  # some servers reject response_format
                        body.pop("response_format")
                        resp = await client.post("/v1/chat/completions", json=body)
                    resp.raise_for_status()
                    return resp.json()["choices"][0]["message"]["content"]

                body = {
                    "model": s.model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"temperature": 0.7, "num_predict": 400},
                }
                if structured:
                    body["format"] = HAUNTING_SCHEMA
                resp = await client.post("/api/chat", json=body)
                resp.raise_for_status()
                return resp.json()["message"]["content"]
        except httpx.TimeoutException as exc:
            raise BrainError(f"{s.model} took longer than {timeout:.0f}s") from exc
        except httpx.HTTPStatusError as exc:
            raise BrainError(f"{s.llm_url} answered {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise BrainError(f"cannot reach {s.llm_url}: {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise BrainError(f"unexpected reply shape from {s.llm_url}: {exc}") from exc

    async def haunt(self, prompt: str) -> Dict[str, str]:
        """Ask Gemma for {lament, diagnosis, fix} about a dead command."""
        content = await self._chat(self._messages(prompt), self.settings.llm_timeout, structured=True)
        return parse_reply(content)

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
