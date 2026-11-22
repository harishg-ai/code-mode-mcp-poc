"""Code Mode MCP server (stdio). Exposes the whole fake API through two tools:
  search  - model writes JS that filters the OpenAPI spec
  execute - model writes JS that calls the API via api.request()

Run: python -m acme.server
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from .api import AcmeApi
from .sandbox import run_in_sandbox
from .spec import build_spec
from .tools_text import EXECUTE_DESCRIPTION, SEARCH_DESCRIPTION, tool_text
from .write_policy import create_approver, describe_request

# The secret lives only in this process. Sandboxed code never sees it.
TOKEN = os.environ.get("ACME_TOKEN", "demo-secret-token")
api = AcmeApi(TOKEN)
SPEC_JSON = json.dumps(build_spec())

server = MCPServer(name="acme-code-mode", version="0.2.0")

Code = Annotated[str, Field(description="async arrow function")]


class Approval(BaseModel):
    approve: bool = Field(title="Allow this request")


def _result(out: dict[str, Any]) -> str:
    text, is_error = tool_text(out)
    if is_error:
        raise ToolError(text)
    return text


@server.tool(name="search", description=SEARCH_DESCRIPTION)
async def search(code: Code) -> str:
    return _result(await run_in_sandbox(code, spec_json=SPEC_JSON))


@server.tool(name="execute", description=EXECUTE_DESCRIPTION)
async def execute(code: Code, ctx: Context) -> str:
    async def ask_user(opts: dict[str, Any]) -> bool:
        # Ask the person, not the model, before a write is forwarded. Clients
        # without elicitation support get no prompt, so the write is blocked.
        caps = ctx.request_context.session.client_capabilities
        if not caps or not caps.elicitation:
            return False
        r = await ctx.elicit(
            f"Code written by the model wants to make a write call:\n{describe_request(opts)}\nAllow it?",
            schema=Approval,
        )
        return r.action == "accept" and bool(r.data and r.data.approve)

    async def on_request(opts: dict[str, Any]) -> Any:
        if not isinstance(opts.get("path"), str):
            raise ValueError("path must be a string")
        return api.handle({
            "method": opts.get("method") or "GET",
            "path": opts["path"],
            "query": opts.get("query"),
            "body": opts.get("body"),
            "headers": {"authorization": f"Bearer {TOKEN}"},
        })

    return _result(await run_in_sandbox(code, on_request=on_request, approve=create_approver(ask=ask_user)))


if __name__ == "__main__":
    server.run()
