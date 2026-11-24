"""Tool descriptions and result formatting shared by the MCP server and the eval."""

from __future__ import annotations

from typing import Any

SEARCH_DESCRIPTION = """Search the Acme Work API OpenAPI spec by writing JavaScript.
`code` is an async arrow function. A global `spec` holds the full OpenAPI 3.1 document; all schemas are inlined (no $refs).
Return only what you need. Example:
async () => Object.entries(spec.paths).filter(([p]) => p.includes('tasks')).map(([p, ops]) => ({ p, methods: Object.keys(ops) }))"""

EXECUTE_DESCRIPTION = """Call the Acme Work API by writing JavaScript.
`code` is an async arrow function. A global `api` is available:
  api.request({ method: 'GET'|'POST'|'PATCH'|'DELETE', path: string, query?: object, body?: object }): Promise<{ status: number, body: any }>
Paths start with /v1 (see the search tool). Auth is added for you. Lists are paginated: { items, page, per_page, total, total_pages }.
Chain calls, loop over pages and return a small result. No fetch, fs, process or require. 5 s limit, 100 requests per run.
Write calls (POST, PATCH, DELETE) need the user's approval and fail if it is not given."""


def tool_text(out: dict[str, Any]) -> tuple[str, bool]:
    """Returns (text, is_error) for a sandbox result."""
    counts: dict[str, int] = {}
    for t in out.get("trace", []):
        d = t.get("decision", "error")
        if d == "allowed" and isinstance(t.get("status"), int) and t["status"] >= 400:
            d = f"http {t['status']}"
        counts[d] = counts.get(d, 0) + 1
    # Only spell out the breakdown when something was blocked or failed.
    detail = (": " + ", ".join(f"{n} {k}" for k, n in counts.items())) if set(counts) - {"allowed"} else ""
    meta = f"\n\n[sandbox: {out['ms']} ms, {out['requests']} API requests{detail}]"
    logs = "\n[console]\n" + "\n".join(out["logs"]) if out.get("logs") else ""
    if not out["ok"]:
        return f"Error: {out['error']}{logs}{meta}", True
    return out["result"] + logs + meta, False
