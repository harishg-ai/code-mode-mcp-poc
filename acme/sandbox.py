"""Host side of the sandbox.

Each call starts a fresh, locked-down Node process (acme/sandbox_child.mjs),
sends it the model's JavaScript, answers its API requests (adding the secret
token here, never inside the sandbox) and kills it on timeout.

The model writes JavaScript on purpose: Node's `vm` module plus the Node
permission model give a small, testable isolation layer. Python has no
equivalent built in.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream

CHILD = Path(__file__).with_name("sandbox_child.mjs")
MAX_RESULT_CHARS = 6000
MAX_LINE_BYTES = 16 * 1024 * 1024

OnRequest = Callable[[dict[str, Any]], Awaitable[Any]]
Approve = Callable[[dict[str, Any]], Awaitable[bool]]


def _node() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the sandbox (node was not found on PATH)")
    return node


def _child_env() -> dict[str, str]:
    # No secrets in the sandbox's environment. Windows needs SystemRoot for
    # Node to start; it holds no secret.
    import os

    return {k: os.environ[k] for k in ("SystemRoot", "SYSTEMROOT") if k in os.environ}


async def run_in_sandbox(
    code: str,
    spec_json: str | None = None,
    on_request: OnRequest | None = None,
    approve: Approve | None = None,
    timeout_s: float = 5.0,
    max_requests: int = 100,
) -> dict[str, Any]:
    """Run model-written JS. Returns {ok, result | error, logs, requests, ms}.

    `approve(opts)` (optional) runs on the host before each API request is
    forwarded and returns True to allow it. The time limit is paused while it
    waits, so a person has time to answer an approval prompt.
    """
    started = time.monotonic()
    paused = 0.0
    requests = 0
    remaining = timeout_s

    proc = await anyio.open_process(
        [
            _node(),
            # Node permission model: read only the sandbox script's folder; no fs
            # writes, no child_process, no worker_threads, no native addons.
            "--permission",
            f"--allow-fs-read={CHILD.parent}",
            str(CHILD),
        ],
        env=_child_env(),
    )
    stdout = BufferedByteReceiveStream(proc.stdout)

    def finish(out: dict[str, Any]) -> dict[str, Any]:
        out.update(requests=requests, ms=round((time.monotonic() - started - paused) * 1000))
        if paused:
            out["approval_wait_ms"] = round(paused * 1000)
        return out

    async def send(obj: dict[str, Any]) -> None:
        await proc.stdin.send((json.dumps(obj) + "\n").encode())

    try:
        await send({"code": code, "specJson": spec_json, "allowApi": on_request is not None})
        while True:
            t0 = time.monotonic()
            line = b""
            with anyio.move_on_after(remaining):
                try:
                    line = await stdout.receive_until(b"\n", MAX_LINE_BYTES)
                except (anyio.EndOfStream, anyio.IncompleteRead):
                    err = (await proc.stderr.receive(65536)).decode(errors="replace") if proc.stderr else ""
                    return finish({"ok": False, "error": f"sandbox exited ({proc.returncode}) {err.strip()}"})
            remaining -= time.monotonic() - t0
            if not line:
                return finish({"ok": False, "error": f"timed out after {round(timeout_s * 1000)} ms"})

            msg = json.loads(line)
            if msg["type"] == "done":
                logs = json.loads(msg.get("logsJson") or "[]")
                if not msg["ok"]:
                    return finish({"ok": False, "error": msg["error"], "logs": logs})
                text = msg["resultJson"]
                if len(text) > MAX_RESULT_CHARS:
                    text = text[:MAX_RESULT_CHARS] + f"… [truncated, {len(msg['resultJson'])} chars total]"
                return finish({"ok": True, "result": text, "logs": logs})

            if msg["type"] == "request":
                req = json.loads(msg["payload"])
                rid, opts = req["id"], req.get("opts") or {}
                requests += 1
                try:
                    if on_request is None:
                        raise RuntimeError("no API in this tool")
                    if requests > max_requests:
                        raise RuntimeError(f"request limit ({max_requests}) exceeded")
                    if approve is not None:
                        w0 = time.monotonic()
                        allowed = await approve(opts)
                        paused += time.monotonic() - w0
                        if not allowed:
                            method = str(opts.get("method") or "GET").upper()
                            raise RuntimeError(f"blocked: {method} {opts.get('path')} was not approved by the user")
                    t1 = time.monotonic()
                    reply = {"id": rid, "response": await on_request(opts)}
                    remaining -= time.monotonic() - t1
                except Exception as e:  # noqa: BLE001 - every failure goes back to the model code
                    reply = {"id": rid, "error": str(e)}
                await send({"type": "response", "payload": json.dumps(reply)})
    finally:
        if proc.returncode is None:
            proc.kill()
        with anyio.CancelScope(shield=True):
            await proc.aclose()
