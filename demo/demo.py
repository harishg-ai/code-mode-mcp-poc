"""End-to-end demo.

Starts the MCP server over stdio as a real MCP client, measures tool-definition
tokens against a one-tool-per-endpoint server, runs search + execute the way an
agent would, probes the sandbox and tests the write-approval gate.
Writes everything to results/demo-output.md.

Run: python demo/demo.py
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import anyio
import tiktoken
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from acme.api import AcmeApi  # noqa: E402
from acme.spec import build_spec  # noqa: E402

enc = tiktoken.get_encoding("cl100k_base")
out: list[str] = []


def tok(x) -> int:
    return len(enc.encode(x if isinstance(x, str) else json.dumps(x, separators=(",", ":"))))


def log(s: str = "") -> None:
    print(s)
    out.append(s)


def naive_tools() -> list[dict]:
    """One MCP tool per endpoint, generated from the same spec."""
    tools = []
    for path, ops in build_spec()["paths"].items():
        for method, op in ops.items():
            properties, required = {}, []
            for p in op.get("parameters", []):
                properties[p["name"]] = {**p["schema"], **({"description": p["description"]} if p.get("description") else {})}
                if p.get("required"):
                    required.append(p["name"])
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                properties["body"] = body
                required.append("body")
            tools.append({
                "name": op["operationId"],
                "description": f"{op['summary']}. {method.upper()} {path}",
                "inputSchema": {"type": "object", "properties": properties, "required": required},
            })
    return tools


def server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "acme.server"], cwd=str(ROOT),
        env={"ACME_TOKEN": "demo-secret-token", "ACME_WRITES": "ask"},
    )


async def call(session: ClientSession, name: str, code: str) -> tuple[str, bool]:
    r = await session.call_tool(name, {"code": code})
    return "\n".join(c.text for c in r.content if c.type == "text"), bool(r.is_error)


async def main() -> None:
    log("# Code Mode MCP POC: run output")
    log(f"Run at {datetime.now(timezone.utc).isoformat(timespec='seconds')} on Python {platform.python_version()}")

    answers: list[bool] = []

    async def on_elicit(ctx, params):
        return types.ElicitResult(action="accept", content={"approve": answers.pop(0)})

    async with stdio_client(server_params()) as (r, w):
        async with ClientSession(r, w, elicitation_callback=on_elicit) as session:
            await session.initialize()

            # 1. Context cost of tool definitions.
            tools = (await session.list_tools()).tools
            code_mode = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools]
            naive = naive_tools()
            cm_tokens, naive_tokens = tok(code_mode), tok(naive)
            log("\n## 1. Tool definitions in context (cl100k_base tokens)")
            log("| Server | Tools | Tokens |\n|---|---|---|")
            log(f"| One tool per endpoint | {len(naive)} | {naive_tokens:,} |")
            log(f"| Code Mode (search + execute) | {len(code_mode)} | {cm_tokens:,} |")
            log(f"\nReduction: {100 * (1 - cm_tokens / naive_tokens):.1f}%. The Code Mode cost stays flat as the API grows; "
                "the per-endpoint cost grows with every endpoint.")

            # 2. search: discover the right endpoints without loading the spec.
            log("\n## 2. `search`: find task endpoints")
            search_code = """async () => Object.entries(spec.paths)
  .filter(([p]) => p.includes('tasks'))
  .flatMap(([p, ops]) => Object.entries(ops).map(([m, op]) => ({ route: m.toUpperCase() + ' ' + p, summary: op.summary,
    filters: (op.parameters || []).filter(x => x.in === 'query').map(x => x.name) })))"""
            log("```js\n" + search_code + "\n```")
            s, _ = await call(session, "search", search_code)
            log("Result:\n```json\n" + s + "\n```")
            log(f"Spec size: {tok(build_spec()):,} tokens. Search result: {tok(s):,} tokens.")

            # 3. execute: a multi-step question answered in one call.
            log('\n## 3. `execute`: "Who has the most overdue high-priority tasks?"')
            exec_code = """async () => {
  const today = '2026-11-21';
  const tasks = [];
  for (let page = 1; ; page++) {
    const r = await api.request({ method: 'GET', path: '/v1/tasks', query: { priority: 'high', per_page: 50, page } });
    tasks.push(...r.body.items);
    if (page >= r.body.total_pages) break;
  }
  const overdue = tasks.filter(t => t.status !== 'done' && t.due_date < today);
  const byUser = {};
  for (const t of overdue) byUser[t.assignee_id] = (byUser[t.assignee_id] || 0) + 1;
  const top = Object.entries(byUser).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const people = await Promise.all(top.map(async ([id, n]) => {
    const u = (await api.request({ method: 'GET', path: '/v1/users/' + id })).body;
    return { name: u.name, email: u.email, overdue_high_priority: n };
  }));
  return { checked: tasks.length, overdue: overdue.length, top: people };
}"""
            log("```js\n" + exec_code + "\n```")
            e, _ = await call(session, "execute", exec_code)
            log("Result:\n```\n" + e + "\n```")

            # Same question with classic tool calls: every raw page and user
            # record would pass through the model's context. Same seed, same data.
            local = AcmeApi("t")
            auth = {"authorization": "Bearer t"}
            raw_tokens, classic_calls, page = 0, 0, 1
            while True:
                body = local.handle({"method": "GET", "path": "/v1/tasks", "headers": auth,
                                     "query": {"priority": "high", "per_page": 50, "page": page}})["body"]
                raw_tokens += tok(body)
                classic_calls += 1
                if page >= body["total_pages"]:
                    break
                page += 1
            classic_calls += 3
            raw_tokens += 3 * tok(local.handle({"method": "GET", "path": "/v1/users/1", "headers": auth})["body"])
            log("\n| Approach | Model round trips | Tool-result tokens in context |\n|---|---|---|")
            log(f"| Classic tool calls | {classic_calls} | {raw_tokens:,} |")
            log(f"| Code Mode execute | 1 | {tok(e):,} |")

            # 4. Sandbox probes. Each should fail.
            log("\n## 4. Sandbox probes (each should be blocked)")
            probes = {
                "read secrets from env": "async () => typeof process === 'undefined' ? 'no process global' : process.env",
                "require a module": "async () => require('fs')",
                "dynamic import": "async () => (await import('node:fs')).readFileSync('package.json', 'utf8')",
                "network fetch": "async () => fetch('https://example.com')",
                "eval": "async () => eval('1+1')",
                "Function-constructor escape": "async () => api.request.constructor('return process')().env",
                "prototype-chain escape": "async () => ({}).constructor.constructor('return this')()",
                "infinite loop": "async () => { while (true) {} }",
                "read the bearer token": "async () => (await api.request({ path: '/v1/users/1' })).body",
            }
            log("| Probe | Outcome |\n|---|---|")
            for name, code in probes.items():
                text, is_error = await call(session, "execute", code)
                first = text.split("\n")[0].replace("|", "\\|")[:110]
                leaked = "demo-secret-token" in text
                log(f"| {name} | {'LEAKED TOKEN' if leaked else ('blocked: ' if is_error else 'ran: ') + first} |")

            # 5. Write approval. Generated code tries a DELETE the user never asked for.
            log("\n## 5. Write approval (host-side, outside the model)")
            delete = "async () => (await api.request({ method: 'DELETE', path: '/v1/tasks/7' })).status"
            log("```js\n" + delete + "\n```")
            log("| Case | Outcome |\n|---|---|")
            answers[:] = [False]
            text, is_error = await call(session, "execute", delete)
            log(f"| User declines in the approval prompt | {'blocked' if is_error else 'ran'}: {text.splitlines()[0][:100]} |")
            answers[:] = [True]
            text, is_error = await call(session, "execute", delete)
            log(f"| User approves | {'blocked' if is_error else 'ran'}: status {text.splitlines()[0]} |")
            loop_code = ("async () => { const r = []; for (const id of [8, 9, 10]) { try { r.push((await api.request("
                         "{ method: 'DELETE', path: '/v1/tasks/' + id })).status) } catch (e) { r.push(e.message) } } return r }")
            answers[:] = [False]
            text, _ = await call(session, "execute", loop_code)
            log(f"| Loop of 3 deletes, user declines the first | {text.splitlines()[0][:150].replace('|', '/')} |")

    # A client with no elicitation support cannot be asked, so writes fail closed.
    async with stdio_client(server_params()) as (r, w):
        async with ClientSession(r, w) as plain:
            await plain.initialize()
            text, is_error = await call(plain, "execute", delete)
            log(f"| Client without approval support | {'blocked' if is_error else 'ran'}: {text.splitlines()[0][:100]} |")

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "demo-output.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\nSaved results/demo-output.md")


if __name__ == "__main__":
    anyio.run(main)
