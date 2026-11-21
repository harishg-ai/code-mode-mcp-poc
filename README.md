# Code Mode MCP POC

A local proof of concept of the pattern in Cloudflare's post
[Code Mode: give agents an entire API in 1,000 tokens](https://blog.cloudflare.com/code-mode-mcp/).

Instead of one MCP tool per API endpoint, the server exposes two tools:

- `search`: the model writes JavaScript that filters the OpenAPI spec (global `spec`).
- `execute`: the model writes JavaScript that calls the API through `api.request()`.

The code runs in a sandbox, so only the final, small result returns to the model.

## Layout

| File | Role |
|---|---|
| `src/api.js` | Fake "Acme Work" REST API: 16 resources, 82 operations, in memory, deterministic seed |
| `src/spec.js` | OpenAPI 3.1 spec for it, all schemas inlined |
| `src/sandbox.js` | Host side: forks a locked-down process per call, answers API requests, adds the token, enforces timeouts and a request cap |
| `src/sandbox-child.mjs` | Sandbox: `vm` context with no host objects reachable, string code generation disabled |
| `src/mcp-server.js` | stdio MCP server with the `search` and `execute` tools |
| `demo/demo.js` | Real MCP client: token comparison, sample search and execute, sandbox probes |

## Run

```powershell
npm install
npm run demo
```

The output is saved to `results/demo-output.md`.

## Use it from Claude Code

```powershell
claude mcp add acme-code-mode -- node "<full path>\src\mcp-server.js"
```

Then ask, for example: "Using acme-code-mode, which customers on the enterprise tier have overdue invoices?"

## Sandbox layers

1. Fresh Node process per call, started with `--permission` (read-only access to `src/`, no writes, no child processes, no workers) and an empty environment.
2. Inside it, a `vm` context with `codeGeneration: { strings: false, wasm: false }`. The bridge functions are captured in closures and deleted from the global object before model code runs.
3. The bearer token stays in the MCP server process. The sandbox never receives it.
4. 2 s CPU timeout for synchronous code, 5 s wall-clock kill, 100 API requests per run, results truncated at 6,000 characters.

**Limits:** `node:vm` is not a security boundary on its own. This POC layers it with a separate process and the Node permission model, but it is not production isolation. The Node permission model in Node 24 does not block outbound network from the child process; the `vm` context just has no `fetch`. For production, use V8 isolates built for untrusted code, such as Cloudflare's Worker Loader or `isolated-vm`, or a container or microVM sandbox.
