# Code Mode MCP POC

A local proof of concept of the pattern in Cloudflare's post
[Code Mode: give agents an entire API in 1,000 tokens](https://blog.cloudflare.com/code-mode-mcp/).

Instead of one MCP tool per API endpoint, the server exposes two tools:

- `search`: the model writes JavaScript that filters the OpenAPI spec (global `spec`).
- `execute`: the model writes JavaScript that calls the API through `api.request()`.

The code runs in a sandbox, so only the final, small result returns to the model.

The server, fake API, demo and eval are Python. The model still writes JavaScript, because Node's `vm` module and permission model give a small, testable sandbox. Python has nothing equivalent built in.

## Layout

| File | Role |
|---|---|
| `acme/api.py` | Fake "Acme Work" REST API: 16 resources, 82 operations, in memory, deterministic seed |
| `acme/spec.py` | OpenAPI 3.1 spec for it, all schemas inlined |
| `acme/sandbox.py` | Host side: starts a locked-down Node process per call, answers API requests, adds the token, enforces time and request limits |
| `acme/sandbox_child.mjs` | Sandbox: `vm` context with no host objects reachable, string code generation disabled |
| `acme/write_policy.py` | Approval gate for POST/PATCH/DELETE calls made by generated code |
| `acme/server.py` | stdio MCP server with the `search` and `execute` tools (MCP Python SDK 2.x) |
| `demo/demo.py` | Real MCP client: token comparison, sample search and execute, sandbox probes, approval tests |
| `eval/eval.py` | Accuracy eval: the same questions through 82 tools and through Code Mode, scored against ground truth |

## Run

Needs Python 3.11+ and Node.js 22+ on `PATH`.

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\pip install -e .
```

```powershell
.\.venv\Scripts\python demo\demo.py
```

The output is saved to `results/demo-output.md`.

## Use it from Claude Code

```powershell
claude mcp add acme-code-mode -- "<full path>\.venv\Scripts\python.exe" -m acme.server
```

Run it from the repo folder, or set the server's working directory to it. Then ask, for example: "Using acme-code-mode, which customers on the enterprise tier have overdue invoices?"

## Write approval

Generated code can call any method, so a model could delete data the user never asked about. The server checks every request before forwarding it, outside the model:

- GET requests go through.
- POST, PATCH and DELETE pause the sandbox and ask the user through MCP elicitation. Only an explicit yes forwards the call.
- If the client cannot show an approval prompt, writes are blocked (fail closed).
- After one "no", the rest of that run's writes are blocked, so a loop cannot keep asking.
- The time limit is paused while the prompt waits.

`ACME_WRITES=deny` blocks all writes. `ACME_WRITES=allow` turns the gate off, for trusted scripted use only.

## Accuracy eval

Copy `.env.example` to `.env` (git-ignored). The default uses free open-weight models on OpenRouter: set `OPENROUTER_API_KEY`, then list the `:free` models (tool support is marked) and set `LLM_MODEL` in `.env`:

```powershell
.\.venv\Scripts\python eval\eval.py --models
```

```powershell
.\.venv\Scripts\python eval\eval.py
```

It asks 12 questions with known answers, each through both tool designs with the same model, and writes `results/eval.md` and `results/eval.json`. `LLM_PROVIDER` is `xai` or `gemini`; any OpenAI-compatible endpoint works through `LLM_BASE_URL`. Use `--dry` to check ground truth without API calls, and `--only 1,6,9` to run a subset.

## Sandbox layers

1. Fresh Node process per call, started with `--permission` (read-only access to `acme/`, no writes, no child processes, no workers) and an environment with no secrets.
2. Inside it, a `vm` context with `codeGeneration: { strings: false, wasm: false }`. The bridge functions are captured in closures and deleted from the global object before model code runs.
3. The bearer token stays in the MCP server process. The sandbox never receives it.
4. 2 s CPU timeout for synchronous code, 5 s wall-clock limit (paused during approval prompts), 100 API requests per run, results truncated at 6,000 characters.

**Limits:** `node:vm` is not a security boundary on its own. This POC layers it with a separate process and the Node permission model, but it is not production isolation. The Node permission model does not block outbound network from the child process; the `vm` context just has no `fetch`. For production, use V8 isolates built for untrusted code, such as Cloudflare's Worker Loader or `isolated-vm`, or a container or microVM sandbox.
