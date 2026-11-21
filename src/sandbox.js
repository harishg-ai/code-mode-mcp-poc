// Host side of the sandbox. Each call forks a fresh, locked-down Node
// process, sends it the model's code, answers its API requests (adding the
// secret token here, never inside the sandbox) and kills it on timeout.
import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const CHILD = fileURLToPath(new URL('./sandbox-child.mjs', import.meta.url));
const MAX_RESULT_CHARS = 6000;

export function runInSandbox({ code, specJson, onRequest, timeoutMs = 5000, maxRequests = 100 }) {
  return new Promise((resolve) => {
    const started = Date.now();
    let requests = 0;
    const child = fork(CHILD, [], {
      // Node permission model: read only the sandbox script's folder; no fs
      // writes, no child_process, no worker_threads, no native addons.
      execArgv: ['--permission', `--allow-fs-read=${path.dirname(CHILD)}`],
      env: {}, // no secrets in the sandbox's environment
      stdio: ['ignore', 'ignore', 'pipe', 'ipc'],
      serialization: 'json',
    });
    let stderr = '';
    child.stderr.on('data', (d) => (stderr += d));

    let settled = false;
    const finish = (out) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (child.connected) child.kill();
      resolve({ ...out, requests, ms: Date.now() - started });
    };
    const timer = setTimeout(() => finish({ ok: false, error: `timed out after ${timeoutMs} ms` }), timeoutMs);

    child.on('message', async (m) => {
      if (m.type === 'request') {
        const { id, opts } = JSON.parse(m.payload);
        requests++;
        let reply;
        if (requests > maxRequests) reply = { id, error: `request limit (${maxRequests}) exceeded` };
        else {
          try {
            reply = { id, response: await onRequest(opts) };
          } catch (e) {
            reply = { id, error: String(e?.message ?? e) };
          }
        }
        if (child.connected) child.send({ type: 'response', payload: JSON.stringify(reply) });
      } else if (m.type === 'done') {
        const logs = JSON.parse(m.logsJson || '[]');
        if (!m.ok) return finish({ ok: false, error: m.error, logs });
        let text = m.resultJson;
        if (text.length > MAX_RESULT_CHARS) text = text.slice(0, MAX_RESULT_CHARS) + `… [truncated, ${m.resultJson.length} chars total]`;
        finish({ ok: true, result: text, logs });
      }
    });
    child.on('exit', (codeNum) => finish({ ok: false, error: `sandbox exited (${codeNum}) ${stderr.trim()}` }));
    child.send({ code, specJson, allowApi: Boolean(onRequest) });
  });
}
