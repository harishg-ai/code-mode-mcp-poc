"""Host-side policy for API calls made by model-written code.

Reads go through. Writes (POST, PUT, PATCH, DELETE) need a decision made
outside the model: the sandboxed code cannot see or change this policy.

The policy judges the *resolved* request (acme.api.Resolved): the router has
already turned the raw path into the canonical operation that will run, and
that same object is executed after approval. So the prompt always shows what
actually runs, never the model's raw string.

ACME_WRITES:
  ask   (default) ask the user through MCP elicitation; block if the client cannot ask
  deny  block every write
  allow forward every write (only for trusted, scripted use)
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

Verdict = bool | str  # False = blocked; "allowed" / "approved" = let through


def is_write(req: Any) -> bool:
    """True if running this request would change data."""
    if hasattr(req, "has_side_effects"):
        return bool(req.has_side_effects)  # resolved: routing errors change nothing
    return str((req or {}).get("method") or "GET").upper() in WRITE_METHODS


def describe_request(req: Any) -> str:
    """The text shown in the approval prompt."""
    if hasattr(req, "describe"):
        return req.describe()
    method = str(req.get("method") or "GET").upper()
    body = "" if req.get("body") is None else f"\nBody: {json.dumps(req['body'])[:500]}"
    return f"{method} {req.get('path')}{body}"


def create_approver(
    ask: Callable[[Any], Awaitable[bool]] | None = None,
    mode: str | None = None,
) -> Callable[[Any], Awaitable[Verdict]]:
    """Returns an approve(req) function for run_in_sandbox, one per tool call.

    `ask(req)` must return True only on an explicit yes from the user.
    """
    mode = mode or os.environ.get("ACME_WRITES", "ask")
    denied = False

    async def approve(req: Any) -> Verdict:
        nonlocal denied
        if not is_write(req) or mode == "allow":
            return "allowed"
        if mode == "deny" or ask is None or denied:
            return False
        try:
            ok = await ask(req) is True
        except Exception:  # noqa: BLE001 - any failure to ask means no
            ok = False
        # One "no" blocks the rest of this run, so a loop cannot keep asking.
        if not ok:
            denied = True
            return False
        return "approved"

    return approve
