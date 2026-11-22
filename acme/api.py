"""A fake "Acme Work" REST API: in-memory, deterministic seed data.

It stands in for a large real API (Cloudflare's has 2,500+ endpoints).
Every resource gets list/create/get/update/delete, plus a few extra routes.
The seed generator matches the original JavaScript version value for value,
so earlier results stay comparable.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlparse

RESOURCES: dict[str, dict[str, Any]] = {
    "projects": {"name": "string", "status": ["active", "archived"], "owner_id": "ref:users"},
    "tasks": {
        "title": "string",
        "status": ["todo", "in_progress", "done"],
        "priority": ["low", "medium", "high"],
        "project_id": "ref:projects",
        "assignee_id": "ref:users",
        "due_date": "date",
    },
    "users": {"name": "string", "email": "string", "role": ["admin", "member", "guest"], "team_id": "ref:teams"},
    "teams": {"name": "string"},
    "comments": {"body": "string", "task_id": "ref:tasks", "author_id": "ref:users"},
    "labels": {"name": "string", "color": "string"},
    "milestones": {"name": "string", "project_id": "ref:projects", "due_date": "date"},
    "customers": {"name": "string", "tier": ["free", "pro", "enterprise"], "mrr": "number"},
    "invoices": {"customer_id": "ref:customers", "amount": "number", "status": ["draft", "sent", "paid", "overdue"]},
    "tickets": {
        "subject": "string",
        "customer_id": "ref:customers",
        "severity": ["sev1", "sev2", "sev3"],
        "status": ["open", "closed"],
    },
    "releases": {"version": "string", "project_id": "ref:projects", "channel": ["stable", "beta"]},
    "webhooks": {"url": "string", "event": ["task.created", "task.done", "invoice.paid"], "active": "boolean"},
    "dns_records": {
        "zone": "string",
        "type": ["A", "AAAA", "CNAME", "TXT", "MX"],
        "name": "string",
        "content": "string",
        "ttl": "number",
    },
    "firewall_rules": {
        "zone": "string",
        "expression": "string",
        "action": ["block", "challenge", "allow", "log"],
        "enabled": "boolean",
    },
    "rate_limits": {"zone": "string", "path": "string", "requests_per_minute": "number", "action": ["block", "challenge"]},
    "audit_logs": {"actor_id": "ref:users", "action": "string", "resource": "string"},
}

SEED_COUNT = {"tasks": 230, "users": 18, "teams": 4, "projects": 9, "comments": 120, "customers": 40, "invoices": 90, "tickets": 60}


def _rng(seed: int):
    """Small deterministic PRNG (32-bit LCG) so every run sees the same data."""
    state = seed & 0xFFFFFFFF

    def rand() -> float:
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 2**32

    return rand


def _seed() -> dict[str, list[dict[str, Any]]]:
    rand = _rng(42)

    def pick(arr):
        return arr[math.floor(rand() * len(arr))]

    db: dict[str, list[dict[str, Any]]] = {r: [] for r in RESOURCES}
    # Seed referenced resources first so refs point at real ids.
    first = ["teams", "users", "projects", "customers"]
    order = first + [r for r in RESOURCES if r not in first]
    for r in order:
        for i in range(1, SEED_COUNT.get(r, 12) + 1):
            row: dict[str, Any] = {"id": i}
            for field, kind in RESOURCES[r].items():
                if isinstance(kind, list):
                    row[field] = pick(kind)
                elif kind.startswith("ref:"):
                    target = db[kind[4:]]
                    row[field] = pick(target)["id"] if target else None
                elif kind == "date":
                    row[field] = (date(2026, 10, 1) + timedelta(days=math.floor(rand() * 120))).isoformat()
                elif kind == "number":
                    row[field] = math.floor(rand() * 5000 + 0.5)  # JS Math.round
                elif kind == "boolean":
                    row[field] = rand() > 0.3
                else:
                    row[field] = f"{r[:-1]} {field} {i}"
            db[r].append(row)
    return db


def js_str(v: Any) -> str:
    """String(v) as JavaScript writes it, so filters match the same rows."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _num(v: Any) -> float | None:
    """Number(v) as JavaScript reads it; None stands in for NaN."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return None


class AcmeApi:
    def __init__(self, token: str):
        self._token = token
        self._db = _seed()
        self.calls = 0

    def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        """Router. `req` = {method, path, query, body, headers}."""
        self.calls += 1
        if (req.get("headers") or {}).get("authorization") != f"Bearer {self._token}":
            return _json(401, {"error": "unauthorized"})
        method = str(req.get("method") or "GET").upper()
        url = urlparse(str(req.get("path", "")))
        query = {**dict(parse_qsl(url.query)), **(req.get("query") or {})}
        parts = [p for p in url.path.split("/") if p]
        if not parts or parts[0] != "v1":
            return _json(404, {"error": "not found"})
        resource, rid, sub = (parts[1:] + [None, None, None])[:3]
        table = self._db.get(resource) if resource else None
        if table is None:
            return _json(404, {"error": f"unknown resource {resource}"})
        num_id = _num(rid) if rid is not None else None

        # Extra routes.
        if resource == "projects" and rid and sub == "tasks" and method == "GET":
            return _list([t for t in self._db["tasks"] if t["project_id"] == num_id], query)
        if resource == "tasks" and rid and sub == "assign" and method == "POST":
            t = next((x for x in table if x["id"] == num_id), None)
            if t is None:
                return _json(404, {"error": "not found"})
            t["assignee_id"] = (req.get("body") or {}).get("assignee_id")
            return _json(200, t)
        if sub:
            return _json(404, {"error": "not found"})

        if not rid:
            if method == "GET":
                return _list(table, query)
            if method == "POST":
                row = {"id": max((x["id"] for x in table), default=0) + 1, **(req.get("body") or {})}
                table.append(row)
                return _json(201, row)
            return _json(405, {"error": "method not allowed"})
        idx = next((i for i, x in enumerate(table) if x["id"] == num_id), -1)
        if idx < 0:
            return _json(404, {"error": "not found"})
        if method == "GET":
            return _json(200, table[idx])
        if method == "PATCH":
            table[idx].update(req.get("body") or {})
            table[idx]["id"] = int(num_id)
            return _json(200, table[idx])
        if method == "DELETE":
            table.pop(idx)
            return _json(204, None)
        return _json(405, {"error": "method not allowed"})


def _json(status: int, body: Any) -> dict[str, Any]:
    return {"status": status, "body": body}


def _list(rows: list[dict[str, Any]], query: dict[str, Any]) -> dict[str, Any]:
    """Paginated list with exact-match filters on any field: ?status=done&page=2"""
    page = max(1, int(_num(query.get("page")) or 1))
    per_page = min(50, max(1, int(_num(query.get("per_page")) or 20)))
    filters = [(k, v) for k, v in query.items() if k not in ("page", "per_page")]
    filtered = [r for r in rows if all(js_str(r.get(k, "undefined")) == js_str(v) for k, v in filters)]
    total_pages = max(1, math.ceil(len(filtered) / per_page))
    return _json(
        200,
        {
            "items": filtered[(page - 1) * per_page : page * per_page],
            "page": page,
            "per_page": per_page,
            "total": len(filtered),
            "total_pages": total_pages,
        },
    )
