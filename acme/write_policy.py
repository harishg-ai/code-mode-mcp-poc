"""Host-side policy for API calls made by model-written code.

Reads go through. Writes (POST, PUT, PATCH, DELETE) need a decision made
outside the model: the sandboxed code cannot see or change this policy.

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


def is_write(opts: dict[str, Any]) -> bool:
    return str(opts.get("method") or "GET").upper() in WRITE_METHODS


def describe_request(opts: dict[str, Any]) -> str:
    method = str(opts.get("method") or "GET").upper()
    body = "" if opts.get("body") is None else f"\nBody: {json.dumps(opts['body'])[:500]}"
    return f"{method} {opts.get('path')}{body}"


def create_approver(
    ask: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
    mode: str | None = None,
) -> Callable[[dict[str, Any]], Awaitable[bool]]:
    """Returns an approve(opts) function for run_in_sandbox, one per tool call.

    `ask(opts)` must return True only on an explicit yes from the user.
    """
    mode = mode or os.environ.get("ACME_WRITES", "ask")
    denied = False

    async def approve(opts: dict[str, Any]) -> bool:
        nonlocal denied
        if not is_write(opts) or mode == "allow":
            return True
        if mode == "deny" or ask is None or denied:
            return False
        try:
            ok = await ask(opts) is True
        except Exception:  # noqa: BLE001 - any failure to ask means no
            ok = False
        # One "no" blocks the rest of this run, so a loop cannot keep asking.
        if not ok:
            denied = True
        return ok

    return approve
