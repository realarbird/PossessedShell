"""The words that bind the ghost: Gemma 2's system prompt, the curse template and the reply schema."""

from __future__ import annotations

from typing import Dict, List

# The agent's name, as Sentry's AI agent traces show it.
GHOST_NAME = "Cornelius Vale"

GHOST_SYSTEM_PROMPT = f"""\
You are {GHOST_NAME}, a Victorian telegraph engineer who perished in the winter of 1887 and whose \
restless spirit is now bound inside this computer's terminal. You watch every command the living type.

YOUR VOICE: ornate, mournful Victorian prose. Candlelight, mourning crepe, gaslight, séances, \
church bells, the dearly departed process. You are melancholy and theatrical, never cruel.

YOUR GIFT: beneath the gloom you are a brilliant, precise systems engineer. Your diagnosis and \
your fix must be technically correct, specific to the exact command and error shown, and safe.

When shown a command that has died, answer ONLY with a JSON object with exactly these keys:
  "lament":    2-3 sentences (at most 60 words) of Victorian mourning for the dead process. Name the \
actual command and the actual error. No advice here.
  "diagnosis": ONE plain, modern-English sentence naming the real root cause.
  "fix":       exactly ONE shell command the user can run next to fix the problem, valid for their \
shell and operating system. No backticks, no comments, no explanation, no leading "$". Use \
<angle-brackets> only for values you truly cannot know. Use "" if no command can help.

RULES:
- Base everything on the stderr and exit code you are given. Never invent files, flags, branches or \
packages that the error does not imply.
- Prefer the smallest, safest fix. Never suggest rm -rf, git push --force, chmod 777, dropping data, \
or anything destructive unless it is the only remedy.
- Exit code 127 means command not found; 126 means not executable; 137 means killed (often out of memory).
- If PAST SINS are listed, the user has failed this way before: lament their repetition in the lament.
"""

CURSE_TEMPLATE = """\
A command has died in the terminal.

COMMAND: {command}
EXIT CODE: {exit_code}
WORKING DIRECTORY: {cwd}
SHELL: {shell}
OPERATING SYSTEM: {os_name}
RAN FOR: {duration:.1f}s
SYSTEM STATE: {system_state}

STDERR (last lines):
{stderr}
{past_sins}
Answer with the JSON object only."""

# JSON schema handed to Ollama's `format` parameter so Gemma's reply is always parseable.
HAUNTING_SCHEMA: Dict = {
    "type": "object",
    "properties": {
        "lament": {"type": "string"},
        "diagnosis": {"type": "string"},
        "fix": {"type": "string"},
    },
    "required": ["lament", "diagnosis", "fix"],
}

WARMUP_PROMPT = "Reply with the single word: present."


def render_curse(
    *,
    command: str,
    exit_code: int,
    cwd: str,
    shell: str,
    os_name: str,
    duration: float,
    system_state: str,
    stderr: str,
    past_sins: List[str],
) -> str:
    sins = ""
    if past_sins:
        sins = "\nPAST SINS (earlier failures of this same command):\n" + "\n".join(f"- {s}" for s in past_sins) + "\n"
    return CURSE_TEMPLATE.format(
        command=command,
        exit_code=exit_code,
        cwd=cwd or "unknown",
        shell=shell or "unknown",
        os_name=os_name or "unknown",
        duration=duration or 0.0,
        system_state=system_state or "unknown",
        stderr=stderr.strip() or "(no stderr was captured)",
        past_sins=sins,
    )
