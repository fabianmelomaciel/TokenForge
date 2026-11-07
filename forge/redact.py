"""Secret and token redaction for task descriptions.

TokenForge stores task descriptions in plain text, so common credential shapes
are replaced before anything is persisted. Redaction is a safety net, not a
guarantee: callers must never put secrets in ``--task`` in the first place.
"""

from __future__ import annotations

import re
from typing import List, Pattern, Tuple

# Order matters: specific token shapes first, generic assignments second, and
# broad high-entropy blobs last.
_RULES: List[Tuple[Pattern[str], str]] = [
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}\b"),
        "[REDACTED_OPENAI_KEY]",
    ),
    (
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_KEY]",
    ),
    (
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "[REDACTED_SLACK_TOKEN]",
    ),
    (
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "[REDACTED_GOOGLE_KEY]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(https?://)[^/\s:@]+:[^/\s@]+@"),
        r"\1[REDACTED]@",
    ),
]

_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{1,}['\"]?"
)

_HEX_BLOB = re.compile(r"\b[A-Fa-f0-9]{40,}\b")
_BASE64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}\b")


def _replace_assignment(match: "re.Match[str]") -> str:
    return "{}=[REDACTED]".format(match.group(1))


def redact(text: str) -> str:
    """Return ``text`` with common secrets replaced by stable markers."""
    if not text:
        return text

    result = text
    for pattern, replacement in _RULES:
        result = pattern.sub(replacement, result)
    result = _ASSIGNMENT.sub(_replace_assignment, result)
    result = _HEX_BLOB.sub("[REDACTED_HEX]", result)
    result = _BASE64_BLOB.sub("[REDACTED_BASE64]", result)
    return result


def contains_secret(text: str) -> bool:
    """Return ``True`` when redaction would change ``text``."""
    return redact(text) != text
