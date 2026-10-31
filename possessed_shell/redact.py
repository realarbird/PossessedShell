"""Scrub secrets from commands and stderr before anything leaves the machine (Sentry, Backboard).

The local Gemma model sees the raw text; only off-machine destinations get the scrubbed copy.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "[REDACTED]"

_PATTERNS = [
    # key=value / key: value pairs whose key smells secret
    (re.compile(r"(?i)\b([\w.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|auth|credential|private[_-]?key)[\w.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s'\"]+)"),
     r"\1\2" + MASK),
    # --password value / -token value style CLI flags
    (re.compile(r"(?i)(\s--?[\w-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|credential)[\w-]*)(\s+)(\"[^\"]*\"|'[^']*'|[^\s'\"-][^\s'\"]*)"),
     r"\1\2" + MASK),
    # Authorization headers
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + MASK),
    # credentials embedded in URLs: scheme://user:pass@host or scheme://:pass@host
    (re.compile(r"([a-z][a-z0-9+.-]*://)([^/\s:@]*):([^/\s@]+)@", re.I), r"\1\2:" + MASK + "@"),
    # curl -u user:pass / --user user:pass (skip -u scheme://... URIs such as redis-cli -u)
    (re.compile(r"(\s(?:-u|--user)\s+[^/\s:@'\"]+:(?!//))(\"[^\"]*\"|'[^']*'|[^\s'\"]+)"), r"\1" + MASK),
    # well-known token shapes
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),  # AWS access key id
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),  # Google API keys
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), MASK),  # GitHub tokens
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), MASK),  # OpenAI/Anthropic style secret keys
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"), MASK),  # Stripe style secret keys
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), MASK),  # Hugging Face tokens
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), MASK),  # npm tokens
    (re.compile(r"\bpypi-[A-Za-z0-9_-]{30,}\b"), MASK),  # PyPI tokens
    (re.compile(r"\bsntry[su]_[A-Za-z0-9_=-]{30,}\b"), MASK),  # Sentry auth tokens
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), MASK),  # Slack
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), MASK),  # JWT
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), MASK),
    # mysql -pPASSWORD
    (re.compile(r"(\bmysql\S*\s+(?:\S+\s+)*?-p)(\S+)"), r"\1" + MASK),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_obj(obj: Any) -> Any:
    """Recursively redact every string in a JSON-like structure (used on whole Sentry events)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v) for v in obj)
    return obj
