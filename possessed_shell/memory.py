"""Ghost memory on Backboard (opt-in): the ghost remembers your past sins across sessions.

Only active when BACKBOARD_API_KEY is set. Every call has a short timeout and swallows errors,
so a slow or missing network never delays or breaks a haunting. What leaves the machine is a
one-line, secret-scrubbed summary of each failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from possessed_shell.config import Settings
from possessed_shell.models import Failure, Haunting
from possessed_shell.prompts import GHOST_SYSTEM_PROMPT
from possessed_shell.redact import redact

log = logging.getLogger("possessed.memory")

ASSISTANT_NAME = "PossessedShell Ghost"


def summarize(failure: Failure, haunting: Haunting) -> str:
    last_words = next((ln.strip() for ln in reversed(failure.stderr.splitlines()) if ln.strip()), "no last words")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(failure.timestamp))
    where = os.path.basename(failure.cwd.rstrip("/")) or failure.cwd or "?"
    summary = f"{when}: `{failure.command}` died with exit {failure.exit_code} in {where}. Last words: {last_words[:160]}."
    if haunting.fix:
        summary += f" Remedy offered: {haunting.fix}"
    return redact(summary)


class GhostMemory:
    def __init__(self, settings: Settings, client: Any = None, timeout: float = 4.0):
        self.settings = settings
        self.timeout = timeout
        self._client = client
        self._assistant_id: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.backboard_api_key or self._client)

    def _get_client(self) -> Any:
        if self._client is None:
            from backboard import BackboardClient  # optional dependency, imported only when enabled

            self._client = BackboardClient(api_key=self.settings.backboard_api_key, base_url=self.settings.backboard_url, timeout=10)
        return self._client

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.settings.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.settings.ensure_home()
        self.settings.state_path.write_text(json.dumps(state, indent=2))
        try:
            os.chmod(self.settings.state_path, 0o600)
        except OSError:
            pass

    async def assistant_id(self) -> str:
        if self._assistant_id:
            return self._assistant_id
        state = self._load_state()
        if state.get("backboard_assistant_id"):
            self._assistant_id = state["backboard_assistant_id"]
            return self._assistant_id
        client = self._get_client()
        found = await client.list_assistants(name=ASSISTANT_NAME, limit=1)
        if found:
            assistant = found[0]
        else:
            assistant = await client.create_assistant(
                name=ASSISTANT_NAME,
                description="Remembers the commands that died in a haunted terminal.",
                system_prompt=GHOST_SYSTEM_PROMPT,
            )
        self._assistant_id = str(assistant.assistant_id)
        state["backboard_assistant_id"] = self._assistant_id
        self._save_state(state)
        return self._assistant_id

    async def _memories(self) -> List[Any]:
        client = self._get_client()
        response = await client.get_memories(await self.assistant_id(), page=1, page_size=100)
        hauntings = [m for m in response.memories if (m.metadata or {}).get("kind") == "haunting"]
        return sorted(hauntings, key=lambda m: (m.metadata or {}).get("timestamp", 0), reverse=True)

    async def recall(self, command_name: str, limit: int = 5) -> List[str]:
        """Past failures of the same program, newest first. [] on any error."""
        if not self.enabled or not command_name:
            return []
        try:
            memories = await asyncio.wait_for(self._memories(), self.timeout)
        except Exception as exc:  # never let memory break a haunting
            log.warning("backboard recall failed: %s", exc)
            return []
        return [m.content for m in memories if (m.metadata or {}).get("command_name") == command_name][:limit]

    async def inscribe(self, failure: Failure, haunting: Haunting) -> bool:
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            await asyncio.wait_for(
                client.add_memory(
                    await self.assistant_id(),
                    content=summarize(failure, haunting),
                    metadata={
                        "kind": "haunting",
                        "command_name": failure.name,
                        "exit_code": failure.exit_code,
                        "timestamp": failure.timestamp,
                    },
                ),
                self.timeout,
            )
            return True
        except Exception as exc:
            log.warning("backboard inscribe failed: %s", exc)
            return False

    async def sins(self, command_name: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """Everything the ghost remembers (for `possessed-shell sins`). Errors propagate to the CLI."""
        memories = await self._memories()
        if command_name:
            memories = [m for m in memories if (m.metadata or {}).get("command_name") == command_name]
        return [{"content": m.content, **(m.metadata or {})} for m in memories[:limit]]

    async def aclose(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            try:
                await self._client.aclose()
            except Exception:
                pass
