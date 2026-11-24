"""Code Mode MCP server (stdio). Exposes the whole fake API through two tools:
  search  - model writes JS that filters the OpenAPI spec
  execute - model writes JS that calls the API via api.request()

Every API call from the sandbox is resolved once by the router, approved
(writes only) from that resolved object, executed as that same object, and
recorded in a per-call trace on the host.

Run: python -m acme.server
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from .api import AcmeApi, Resolved
from .sandbox import run_in_sandbox
from .spec import build_spec
from .tools_text import EXECUTE_DESCRIPTION, SEARCH_DESCRIPTION, tool_text
from .write_policy import create_approver, describe_request

# The secret lives only in this process. Sandboxed code never sees it.
TOKEN = os.environ.get("ACME_TOKEN", "demo-secret-token")
api = AcmeApi(TOKEN)
SPEC_JSON = json.dumps(build_spec())

# Per-call traces go to stderr (stdout is the MCP channel) and, if set, to a JSONL file.
TRACE_FILE = os.environ.get("ACME_TRACE_FILE")
log = logging.getLogger("acme.trace")
if not log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False

server = MCPServer(name="acme-code-mode", version="0.3.0")

Code = Annotated[str, Field(description="async arrow function")]


class Approval(BaseModel):
    approve: bool = Field(title="Allow this request")


def _record(tool: str, out: dict[str, Any]) -> None:
    """Host-side trace: which calls ran, which were blocked or failed, and how long each took."""
    entry = {"ts": round(time.time(), 3), "tool": tool, "ok": out["ok"], "ms": out["ms"],
             "requests": out["requests"], "trace": out.get("trace", [])}
    if not out["ok"]:
        entry["error"] = str(out.get("error"))[:300]
    line = json.dumps(entry)
    log.info("trace %s", line)
    if TRACE_FILE:
        with open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _result(tool: str, out: dict[str, Any]) -> str:
    _record(tool, out)
    text, is_error = tool_text(out)
    if is_error:
        raise ToolError(text)
    return text


@server.tool(name="search", description=SEARCH_DESCRIPTION)
async def search(code: Code) -> str:
    return _result("search", await run_in_sandbox(code, spec_json=SPEC_JSON))


@server.tool(name="execute", description=EXECUTE_DESCRIPTION)
async def execute(code: Code, ctx: Context) -> str:
    async def ask_user(req: Resolved) -> bool:
        # Ask the person, not the model, before a write is executed. The prompt
        # shows the resolved operation, the same object that will run. Clients
        # without elicitation support get no prompt, so the write is blocked.
        caps = ctx.request_context.session.client_capabilities
        if not caps or not caps.elicitation:
            return False
        r = await ctx.elicit(
            f"Code written by the model wants to make a write call:\n{describe_request(req)}\nAllow it?",
            schema=Approval,
        )
        return r.action == "accept" and bool(r.data and r.data.approve)

    def prepare(opts: dict[str, Any]) -> Resolved:
        if not isinstance(opts.get("path"), str):
            raise ValueError("path must be a string")
        return api.resolve({
            "method": opts.get("method") or "GET",
            "path": opts["path"],
            "query": opts.get("query"),
            "body": opts.get("body"),
            "headers": {"authorization": f"Bearer {TOKEN}"},
        })

    async def on_request(req: Resolved) -> Any:
        return api.execute(req)

    return _result("execute", await run_in_sandbox(
        code, prepare=prepare, on_request=on_request, approve=create_approver(ask=ask_user)))


if __name__ == "__main__":
    server.run()
