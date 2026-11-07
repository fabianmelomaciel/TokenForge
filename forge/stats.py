"""Conservative token-savings estimation.

TokenForge never claims exact savings. A handle is assumed to replace a manual
LLM derivation that would cost ``avg_tokens_manual`` tokens per use; the
reported number keeps only a conservative fraction of that to account for
forging, review and verification overhead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_AVG_TOKENS_MANUAL = 1500
CONSERVATIVE_FACTOR = 0.8


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def estimate_tokens_saved(
    uses: int, avg_tokens_manual: int = DEFAULT_AVG_TOKENS_MANUAL
) -> int:
    """Estimate tokens saved after ``uses`` successful runs."""
    try:
        uses_value = max(0, int(uses))
    except (TypeError, ValueError):
        uses_value = 0
    avg = _positive_int(avg_tokens_manual, DEFAULT_AVG_TOKENS_MANUAL)
    return int(round(uses_value * avg * CONSERVATIVE_FACTOR))


def estimate_label(avg_tokens_manual: Optional[int] = None) -> str:
    """Return the human-readable disclaimer used by ``stats``."""
    avg = _positive_int(avg_tokens_manual, DEFAULT_AVG_TOKENS_MANUAL)
    return (
        "Conservative estimate: {:.0%} of each handle's assumed manual token cost "
        "(default {:,}). Actual savings vary."
    ).format(CONSERVATIVE_FACTOR, avg)


def format_number(value: int) -> str:
    """Format an integer with thousands separators."""
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return "0"


def avg_tokens_for_handle(
    config: Optional[Dict[str, Any]],
    name: str,
    default: Optional[int] = None,
) -> int:
    """Resolve the per-handle manual token assumption.

    Precedence: an explicit per-handle override in ``config.json`` wins; then
    the caller's stored ``default`` (the value recorded in memory for that
    handle); then the global ``config.json`` value; then the built-in default.
    """
    config = config or {}
    handles = config.get("handles")
    if isinstance(handles, dict):
        entry = handles.get(name)
        if isinstance(entry, dict) and "avg_tokens_manual" in entry:
            return _positive_int(
                entry.get("avg_tokens_manual"),
                _positive_int(default, DEFAULT_AVG_TOKENS_MANUAL),
            )
    if default is not None:
        return _positive_int(default, DEFAULT_AVG_TOKENS_MANUAL)
    return _positive_int(config.get("avg_tokens_manual"), DEFAULT_AVG_TOKENS_MANUAL)


def summarize(
    memory_data: Dict[str, Any], config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build totals and per-handle rows from a memory document."""
    handles = memory_data.get("handles")
    if not isinstance(handles, dict):
        handles = {}

    rows: List[Dict[str, Any]] = []
    total_uses = 0
    total_saved = 0

    for name in sorted(handles):
        meta = handles[name]
        if not isinstance(meta, dict):
            continue
        uses = _positive_int(meta.get("uses"), 0)
        if meta.get("uses") is None:
            uses = 0
        avg = avg_tokens_for_handle(config, name, meta.get("avg_tokens_manual"))
        saved = estimate_tokens_saved(uses, avg)
        total_uses += uses
        total_saved += saved
        rows.append(
            {
                "name": name,
                "task": meta.get("task", ""),
                "uses": uses,
                "last_used": meta.get("last_used"),
                "tokens_saved_est": saved,
                "avg_tokens_manual": avg,
                "tags": meta.get("tags", []),
                "danger": bool(meta.get("danger", False)),
            }
        )

    rows.sort(key=lambda row: (-int(row["tokens_saved_est"]), row["name"]))
    return {
        "project": memory_data.get("project", ""),
        "handles": len(rows),
        "total_uses": total_uses,
        "tokens_saved_est": total_saved,
        "estimate": {
            "label": estimate_label(),
            "conservative_factor": CONSERVATIVE_FACTOR,
            "default_avg_tokens_manual": DEFAULT_AVG_TOKENS_MANUAL,
        },
        "per_handle": rows,
    }
