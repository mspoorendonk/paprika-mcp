"""
Audit log for paprika-mcp.

Appends one JSON line per tool call to data/audit.jsonl (who/which client/what/
when/outcome/timing) and aggregates it for a usage view. Identity (Google email
+ client_name) comes from oauth_app.current_identity(), set per request after
bearer-token validation. In stdio/local mode identity is empty.

Used by: src/server.py (the @audited decorator wraps each tool call).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

AUDIT_FILE = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"


def _summarize(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, (list, tuple)):
            out[k] = f"[{len(v)}]"
        elif isinstance(v, str) and len(v) > 40:
            out[k] = v[:40] + "…"
        else:
            out[k] = v
    return out


def record(*, user, client, tool, args, ok, duration_ms, error=None) -> None:
    """Append one audit line. Best-effort: never raise into the tool path."""
    try:
        line = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "user": user, "client": client, "tool": tool,
            "args": _summarize(args), "ok": ok, "duration_ms": duration_ms,
        }
        if error:
            line["error"] = str(error)[:200]
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _outcome(result) -> tuple[bool, str | None]:
    """Derive (ok, error_code) from a tool's return value.

    Supports both error conventions: a CallToolResult with isError=True
    (paprika's typed-error contract, code in structuredContent), and a plain
    dict with an "error" key (movie's convention).
    """
    if getattr(result, "isError", False):
        code = (getattr(result, "structuredContent", None) or {}).get("code")
        return False, code
    if isinstance(result, dict) and result.get("error"):
        return False, result.get("error")
    return True, None


def audited(tool_name: str):
    """Wrap an async tool fn so each call is recorded with the request identity.

    Mirrors movie-mcp's audit.audited (specs.md §7); async because Paprika tools
    await the HTTP client. Identity comes from oauth_app.current_identity(),
    set per request after bearer-token validation.
    """
    def deco(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                from oauth_app import current_identity
            except ImportError:  # imported as a package (e.g. pytest)
                from src.oauth_app import current_identity
            user, client = current_identity()
            t0 = time.time()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                record(user=user, client=client, tool=tool_name, args=kwargs,
                       ok=False, duration_ms=int((time.time() - t0) * 1000),
                       error=str(exc))
                raise
            ok, err = _outcome(result)
            record(user=user, client=client, tool=tool_name, args=kwargs, ok=ok,
                   duration_ms=int((time.time() - t0) * 1000), error=err)
            return result
        return wrapper
    return deco


def stats() -> dict:
    if not AUDIT_FILE.exists():
        return {"total": 0, "per_client": {}, "per_tool": {}, "per_day": {}, "last_seen": {}}
    per_client: Counter = Counter()
    per_tool: Counter = Counter()
    per_day: Counter = Counter()
    last_seen: dict[str, str] = {}
    errors = total = 0
    for raw in AUDIT_FILE.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(raw)
        except Exception:
            continue
        total += 1
        per_client[e.get("client") or "?"] += 1
        per_tool[e.get("tool") or "?"] += 1
        per_day[(e.get("ts") or "")[:10]] += 1
        if e.get("client"):
            last_seen[e["client"]] = e.get("ts")
        if not e.get("ok", True):
            errors += 1
    return {
        "total": total, "errors": errors,
        "error_rate": round(errors / total, 3) if total else 0,
        "per_client": dict(per_client.most_common()),
        "per_tool": dict(per_tool.most_common()),
        "per_day": dict(sorted(per_day.items())),
        "last_seen": last_seen,
    }
