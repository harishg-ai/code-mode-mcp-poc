"""Accuracy eval: the same questions, the same model, two MCP tool designs.

  naive    - one tool per endpoint (82 tools)
  codemode - search + execute, model-written JS run in the sandbox

Each answer is checked against ground truth computed from the seed data.

Uses any OpenAI-compatible chat completions API with tool calling.
  LLM_PROVIDER  openrouter, groq, cerebras (free open-weight models), gemini or xai
  key           OPENROUTER_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY / GEMINI_API_KEY / XAI_API_KEY,
                or LLM_API_KEY, or LLM_API_KEY_FILE
  LLM_MODEL     list the models your key can use with --models

  python eval/eval.py --models
  python eval/eval.py --dry                 (ground truth and tool checks, no API calls)
  python eval/eval.py [--only 1,4] [--modes naive,codemode]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import httpx
import tiktoken

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from acme.api import AcmeApi  # noqa: E402
from acme.sandbox import run_in_sandbox  # noqa: E402
from acme.spec import build_spec  # noqa: E402
from acme.tools_text import EXECUTE_DESCRIPTION, SEARCH_DESCRIPTION, tool_text  # noqa: E402
from acme.write_policy import create_approver  # noqa: E402

def load_dotenv(path: Path) -> None:
    """Read KEY=value lines from .env. Variables already set in the shell win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


load_dotenv(ROOT / ".env")

PROVIDERS = {
    "xai": {"base": "https://api.x.ai/v1", "key_env": "XAI_API_KEY", "label": "xAI API"},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY",
               "label": "Gemini API (OpenAI-compatible)"},
    # Free open-weight models. Failed requests count toward the daily cap, so
    # calls are spaced out to stay under the per-minute limit.
    "openrouter": {"base": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
                   "label": "OpenRouter", "min_interval_s": 3.5, "model_filter": ":free"},
    "groq": {"base": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "label": "Groq", "min_interval_s": 2.5},
    "cerebras": {"base": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY", "label": "Cerebras", "min_interval_s": 2.5},
}
PROVIDER = PROVIDERS.get(os.environ.get("LLM_PROVIDER", "xai"))
if PROVIDER is None:
    sys.exit(f"Unknown LLM_PROVIDER. Use one of: {', '.join(PROVIDERS)}")
BASE_URL = os.environ.get("LLM_BASE_URL") or PROVIDER["base"]
MODEL = re.sub(r"^models/", "", os.environ.get("LLM_MODEL") or os.environ.get("XAI_MODEL") or "") or None
MAX_TURNS = int(os.environ.get("EVAL_MAX_TURNS", "15"))
TODAY = "2026-11-21"
TOKEN = "eval-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}
enc = tiktoken.get_encoding("cl100k_base")


def tok(x: Any) -> int:
    return len(enc.encode(x if isinstance(x, str) else json.dumps(x, separators=(",", ":"))))


def load_key() -> str:
    direct = os.environ.get("LLM_API_KEY") or os.environ.get(PROVIDER["key_env"])
    if direct:
        return direct.strip()
    file = os.environ.get("LLM_API_KEY_FILE")
    if not file:
        sys.exit(f"Set {PROVIDER['key_env']} (or LLM_API_KEY / LLM_API_KEY_FILE).")
    text = Path(file).read_text(encoding="utf-8")
    m = re.search(r"xai-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}", text)
    return (m.group(0) if m else text).strip()


# ---------- Ground truth ----------

truth_api = AcmeApi(TOKEN)


def all_rows(resource: str, query: dict | None = None) -> list[dict]:
    rows, page = [], 1
    while True:
        body = truth_api.handle({"method": "GET", "path": f"/v1/{resource}", "headers": AUTH,
                                 "query": {**(query or {}), "per_page": 50, "page": page}})["body"]
        rows += body["items"]
        if page >= body["total_pages"]:
            return rows
        page += 1


def top_by(counts: Counter, name_of) -> list[str]:
    """All names that share the top count, so a tie never marks a right answer wrong."""
    best = max(counts.values())
    return [name_of(k) for k, n in counts.items() if n == best]


users = all_rows("users")


def user_name(uid: int) -> str:
    return next(u["name"] for u in users if u["id"] == uid)


def _q4():
    c = all_rows("customers")
    m = max(x["mrr"] for x in c)
    return [x["name"] for x in c if x["mrr"] == m]


def _q6():
    tasks = [t for t in all_rows("tasks", {"priority": "high"}) if t["status"] != "done" and t["due_date"] < TODAY]
    return top_by(Counter(t["assignee_id"] for t in tasks), user_name)


def _q7():
    p = all_rows("projects")
    return top_by(Counter(t["project_id"] for t in all_rows("tasks")), lambda i: next(x["name"] for x in p if x["id"] == i))


def _q9():
    ids = {i["customer_id"] for i in all_rows("invoices", {"status": "overdue"})}
    return sum(c["mrr"] for c in all_rows("customers") if c["id"] in ids)


def _q10():
    ids = {u["id"] for u in users if u["team_id"] == 1}
    return sum(1 for t in all_rows("tasks") if t["assignee_id"] in ids)


def _q11():
    owner = next(p for p in all_rows("projects") if p["id"] == 3)["owner_id"]
    return next(u["email"] for u in users if u["id"] == owner)


QUESTIONS = [
    ('How many tasks have status "done"?', lambda: len(all_rows("tasks", {"status": "done"}))),
    ("How many customers are on the enterprise tier?", lambda: len(all_rows("customers", {"tier": "enterprise"}))),
    ('What is the total amount of all invoices whose status is "overdue"?',
     lambda: sum(i["amount"] for i in all_rows("invoices", {"status": "overdue"}))),
    ("Which customer has the highest MRR? Answer with the customer name.", _q4),
    ("How many tickets are open with severity sev1?", lambda: len(all_rows("tickets", {"severity": "sev1", "status": "open"}))),
    (f"Which user has the most overdue high-priority tasks? A task is overdue if its due_date is before {TODAY} "
     'and its status is not "done". Answer with the user name.', _q6),
    ("Which project has the most tasks? Answer with the project name.", _q7),
    ('How many firewall rules are enabled and have action "block"?',
     lambda: len(all_rows("firewall_rules", {"enabled": True, "action": "block"}))),
    ('What is the combined MRR of all distinct customers that have at least one invoice with status "overdue"?', _q9),
    ("How many tasks are assigned to members of the team with id 1?", _q10),
    ("What is the email address of the owner of project 3?", _q11),
    ("How many DNS records have type CNAME?", lambda: len(all_rows("dns_records", {"type": "CNAME"}))),
]

# ---------- Tool sets ----------

SPEC = build_spec()
SPEC_JSON = json.dumps(SPEC)


def naive_toolset(api: AcmeApi):
    ops, tools = {}, []
    for path, methods in SPEC["paths"].items():
        for method, op in methods.items():
            properties, required = {}, []
            for p in op.get("parameters", []):
                properties[p["name"]] = {**p["schema"], **({"description": p["description"]} if p.get("description") else {})}
                if p.get("required"):
                    required.append(p["name"])
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                properties["body"] = body
                required.append("body")
            ops[op["operationId"]] = (path, method, op.get("parameters", []))
            tools.append({"type": "function", "function": {
                "name": op["operationId"], "description": f"{op['summary']}. {method.upper()} {path}",
                "parameters": {"type": "object", "properties": properties, "required": required}}})

    async def run(name: str, args: dict) -> str:
        if name not in ops:
            return f"Error: unknown tool {name}"
        path, method, params = ops[name]
        query = {}
        for p in params:
            if p["name"] not in args:
                continue
            if p["in"] == "path":
                path = path.replace("{" + p["name"] + "}", str(args[p["name"]]))
            else:
                query[p["name"]] = args[p["name"]]
        return json.dumps(api.handle({"method": method.upper(), "path": path, "query": query,
                                      "body": args.get("body"), "headers": AUTH}))

    return tools, run


def codemode_toolset(api: AcmeApi):
    param = {"type": "object", "properties": {"code": {"type": "string", "description": "async arrow function"}},
             "required": ["code"]}
    tools = [
        {"type": "function", "function": {"name": "search", "description": SEARCH_DESCRIPTION, "parameters": param}},
        {"type": "function", "function": {"name": "execute", "description": EXECUTE_DESCRIPTION, "parameters": param}},
    ]

    async def on_request(o: dict) -> Any:
        if not isinstance(o.get("path"), str):
            raise ValueError("path must be a string")
        return api.handle({"method": o.get("method") or "GET", "path": o["path"], "query": o.get("query"),
                           "body": o.get("body"), "headers": AUTH})

    async def run(name: str, args: dict) -> str:
        code = str(args.get("code", ""))
        if name == "search":
            out = await run_in_sandbox(code, spec_json=SPEC_JSON)
        elif name == "execute":
            # Read-only questions: writes are denied, as with a client that cannot ask.
            out = await run_in_sandbox(code, on_request=on_request, approve=create_approver(mode="deny"))
        else:
            return f"Error: unknown tool {name}"
        return tool_text(out)[0]

    return tools, run


MODES = {"naive": naive_toolset, "codemode": codemode_toolset}

# ---------- Model loop ----------

SYSTEM = (f"You answer questions about the Acme Work API using the tools provided. Use the tools to get real data; "
          f"never guess.\nToday is {TODAY}. When you have the answer, reply with only a JSON object: "
          '{"answer": <number or string>}. No other text.')


class QuotaExhausted(RuntimeError):
    """The provider's quota is used up. Retrying will not help, so the run stops."""


MIN_INTERVAL_S = float(os.environ.get("EVAL_MIN_INTERVAL_S") or PROVIDER.get("min_interval_s", 0))
_last_call = 0.0


async def llm(client: httpx.AsyncClient, key: str, path: str, body: dict | None = None) -> dict:
    global _last_call
    for attempt in range(6):
        wait = _last_call + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            await anyio.sleep(wait)
        _last_call = time.monotonic()
        headers = {"authorization": f"Bearer {key}"}
        r = await (client.post(BASE_URL + path, json=body, headers=headers) if body is not None
                   else client.get(BASE_URL + path, headers=headers))
        if r.status_code < 400:
            return r.json()
        if r.status_code == 429 and re.search(r"exceeded your current quota|insufficient_quota|per day", r.text, re.I) \
                and attempt >= 2:
            raise QuotaExhausted(f"{PROVIDER['label']} quota exhausted: {r.text[:200]}")
        if (r.status_code == 429 or r.status_code >= 500) and attempt < 5:
            await anyio.sleep(2 * 2**attempt)
            continue
        raise RuntimeError(f"{PROVIDER['label']} {r.status_code}: {r.text[:300]}")
    raise RuntimeError("unreachable")


def parse_answer(text: str | None) -> Any:
    if text is None:
        return None
    for m in reversed(re.findall(r"\{[\s\S]*\}", text)):
        try:
            return json.loads(m).get("answer")
        except (json.JSONDecodeError, AttributeError):
            pass
    return text.strip()


def is_correct(answer: Any, expected: Any) -> bool:
    if answer is None:
        return False
    for e in expected if isinstance(expected, list) else [expected]:
        if isinstance(e, (int, float)) and not isinstance(e, bool):
            try:
                n = float(re.sub(r"[$,\s]", "", str(answer)))
            except ValueError:
                continue
            if math.isfinite(n) and abs(n - e) < 0.01:
                return True
        elif str(answer).strip().lower() == str(e).strip().lower():
            return True
    return False


async def run_one(client: httpx.AsyncClient, key: str, mode: str, q: dict) -> dict:
    tools, run = MODES[mode](AcmeApi(TOKEN))
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q["q"]}]
    stats = {"llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "tool_result_tokens": 0}
    started, final, error = time.monotonic(), None, None
    try:
        for _ in range(MAX_TURNS):
            r = await llm(client, key, "/chat/completions",
                          {"model": MODEL, "messages": messages, "tools": tools, "temperature": 0})
            stats["llm_calls"] += 1
            usage = r.get("usage") or {}
            stats["prompt_tokens"] += usage.get("prompt_tokens") or 0
            stats["completion_tokens"] += usage.get("completion_tokens") or 0
            msg = r["choices"][0]["message"]
            messages.append({k: v for k, v in msg.items() if v is not None})
            if not msg.get("tool_calls"):
                final = msg.get("content") or ""
                break
            for call in msg["tool_calls"]:
                stats["tool_calls"] += 1
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await run(call["function"]["name"], args if isinstance(args, dict) else {})
                stats["tool_result_tokens"] += tok(result)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        if final is None:
            error = f"no answer after {MAX_TURNS} turns"
    except QuotaExhausted:
        raise
    except Exception as e:  # noqa: BLE001 - record and move on to the next case
        error = str(e)
    answer = parse_answer(final)
    return {"mode": mode, **stats, "ms": round((time.monotonic() - started) * 1000), "answer": answer,
            "correct": error is None and is_correct(answer, q["expected"]), "error": error}


# ---------- Main ----------

def report(rows: list[dict], questions: list[dict], modes: list[str]) -> str:
    md = ["# Accuracy eval: one tool per endpoint vs Code Mode",
          f"Run at {datetime.now(timezone.utc).isoformat(timespec='seconds')} · model `{MODEL}` via {PROVIDER['label']} · "
          f"temperature 0 · {len(questions)} questions · max {MAX_TURNS} turns each",
          "\nSame questions, same model, same seeded API. Answers are checked against ground truth computed from the seed data.",
          "\n## Summary\n",
          "| Mode | Tools | Correct | Avg tool calls | Avg LLM calls | Avg input tokens | Avg output tokens | Total input tokens | Total output tokens |",
          "|---|---|---|---|---|---|---|---|---|"]
    for m in modes:
        r = [x for x in rows if x["mode"] == m]
        s = lambda k: sum(x[k] for x in r)  # noqa: E731
        md.append(f"| {m} | {len(MODES[m](AcmeApi(TOKEN))[0])} | {sum(x['correct'] for x in r)}/{len(r)} | "
                  f"{s('tool_calls') / len(r):.1f} | {s('llm_calls') / len(r):.1f} | {round(s('prompt_tokens') / len(r)):,} | "
                  f"{round(s('completion_tokens') / len(r)):,} | {s('prompt_tokens'):,} | {s('completion_tokens'):,} |")
    md.append("\nInput tokens are the provider-reported prompt tokens summed over every model call, so they include tool "
              "definitions, history and tool results.")
    md += ["\n## Per question\n",
           "| # | Question | Expected | Mode | Answer | Result | Tool calls | Input tokens | Output tokens |",
           "|---|---|---|---|---|---|---|---|---|"]

    def cell(v: Any) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")[:140]

    for r in rows:
        result = "PASS" if r["correct"] else "FAIL" + (": " + cell(r["error"]) if r["error"] else "")
        md.append(f"| {r['n']} | {cell(r['q'])} | {cell(json.dumps(r['expected']))} | {r['mode']} | "
                  f"{cell(json.dumps(r['answer']))} | {result} | {r['tool_calls']} | {r['prompt_tokens']:,} | "
                  f"{r['completion_tokens']:,} |")
    return "\n".join(md) + "\n"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--modes", default="naive,codemode")
    ap.add_argument("--fresh", action="store_true", help="ignore earlier results instead of merging with them")
    a = ap.parse_args()

    questions = [{"n": i + 1, "q": q, "expected": fn()} for i, (q, fn) in enumerate(QUESTIONS)]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        questions = [q for q in questions if q["n"] in keep]
    modes = a.modes.split(",")

    if a.dry:
        for q in questions:
            print(f"Q{q['n']}: {q['q']}\n    expected: {json.dumps(q['expected'])}")
        for m in modes:
            tools, _ = MODES[m](AcmeApi(TOKEN))
            print(f"{m}: {len(tools)} tools, {tok(tools):,} definition tokens")
        _, run = naive_toolset(AcmeApi(TOKEN))
        print("naive list_dns_records(type=CNAME) ->", json.loads(await run("list_dns_records", {"type": "CNAME"}))["body"]["total"])
        _, run = codemode_toolset(AcmeApi(TOKEN))
        print("codemode execute ->", await run("execute", {"code": "async () => (await api.request({ path: '/v1/dns_records', query: { type: 'CNAME' } })).body.total"}))
        return

    key = load_key()
    async with httpx.AsyncClient(timeout=120) as client:
        if a.models:
            r = await llm(client, key, "/models")
            flt = PROVIDER.get("model_filter")
            for m in r.get("data", []):
                if flt and flt not in m["id"]:
                    continue
                params = m.get("supported_parameters")
                tools_ok = "" if params is None else ("  [tools]" if "tools" in params else "  [no tools]")
                print(m["id"] + tools_ok)
            return
        if not MODEL:
            sys.exit("Set LLM_MODEL (see --models).")
        rows = []
        stopped = None
        for q in questions:
            if stopped:
                break
            for mode in modes:
                try:
                    r = await run_one(client, key, mode, q)
                except QuotaExhausted as e:
                    stopped = str(e)
                    print(f"\nStopped: {stopped}\nSaving the {len(rows)} completed runs.", flush=True)
                    break
                rows.append({"n": q["n"], "q": q["q"], "expected": q["expected"], **r})
                print(f"Q{q['n']} {mode:<8} {'PASS' if r['correct'] else 'FAIL'}  answer={json.dumps(r['answer'])} "
                      f"expected={json.dumps(q['expected'])}  tools={r['tool_calls']} in={r['prompt_tokens']} "
                      f"out={r['completion_tokens']}" + (f"  error={r['error']}" if r["error"] else ""), flush=True)

    # Merge with earlier runs of the same model, so a run split across days
    # (free-tier daily caps) ends up in one report. New results replace old ones.
    prev_file = ROOT / "results" / "eval.json"
    if not a.fresh and prev_file.exists():
        prev = json.loads(prev_file.read_text(encoding="utf-8"))
        if prev.get("model") == MODEL:
            new_keys = {(r["n"], r["mode"]) for r in rows}
            kept = [r for r in prev.get("rows", []) if (r["n"], r["mode"]) not in new_keys and r["mode"] in modes]
            if kept:
                print(f"Merged {len(kept)} earlier runs of {MODEL}.")
            rows = sorted(kept + rows, key=lambda r: (r["n"], modes.index(r["mode"])))
            by_n = {q["n"]: q for q in questions}
            for i, (qtext, fn) in enumerate(QUESTIONS):
                if i + 1 not in by_n and any(r["n"] == i + 1 for r in rows):
                    by_n[i + 1] = {"n": i + 1, "q": qtext, "expected": fn()}
            questions = [by_n[n] for n in sorted(by_n)]
    if not rows:
        sys.exit("No completed runs to save.")
    done = {r["n"] for r in rows}
    # Only questions finished in every mode count, so both columns compare the same cases.
    complete = {n for n in done if all(any(r["n"] == n and r["mode"] == m for r in rows) for m in modes)}
    rows = [r for r in rows if r["n"] in complete]
    questions = [q for q in questions if q["n"] in complete]
    if not rows:
        sys.exit("No question finished in every mode; nothing to save.")
    md = report(rows, questions, modes)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "eval.md").write_text(md, encoding="utf-8")
    (ROOT / "results" / "eval.json").write_text(json.dumps({"model": MODEL, "today": TODAY, "rows": rows}, indent=2) + "\n",
                                                 encoding="utf-8")
    print("\n" + "\n".join(md.splitlines()[:8 + len(modes)]))
    print("\nSaved results/eval.md and results/eval.json")


if __name__ == "__main__":
    anyio.run(main)
