// Runs inside a separate Node process started with --permission (no fs
// writes, no child processes, no workers) and an empty environment.
// Inside that process, model code runs in a vm context that holds no
// host-realm objects: everything it can reach was created inside the context.
//
// Protocol with the Python host: one JSON object per line.
//   stdin : {"code", "specJson", "allowApi"} first, then {"type":"response","payload"}
//   stdout: {"type":"request","payload"} and finally {"type":"done", ...}
import vm from 'node:vm';
import readline from 'node:readline';

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
const lines = readline.createInterface({ input: process.stdin });
let deliver = null;
let started = false;

lines.on('line', (line) => {
  const msg = JSON.parse(line);
  if (!started) {
    started = true;
    run(msg);
  } else if (msg.type === 'response' && deliver) {
    deliver(msg.payload);
  }
});

async function run({ code, specJson, allowApi }) {
  const context = vm.createContext(Object.create(null), {
    codeGeneration: { strings: false, wasm: false }, // no eval / new Function
  });

  // Host-side bridge functions. They are installed under temporary names,
  // captured into closures by the bootstrap script, then deleted, so model
  // code never holds a reference to a host-realm function.
  context.__send = (json) => out({ type: 'request', payload: json });
  context.__specJson = specJson ?? 'null';
  context.__allowApi = Boolean(allowApi);

  const bootstrap = `
    (() => {
      const send = globalThis.__send;
      const allowApi = globalThis.__allowApi;
      const pending = new Map();
      let nextId = 0;
      const logs = [];
      globalThis.spec = JSON.parse(globalThis.__specJson);
      globalThis.console = {
        log: (...a) => { logs.push(a.map((x) => typeof x === 'string' ? x : JSON.stringify(x)).join(' ')); },
      };
      globalThis.console.error = globalThis.console.log;
      if (allowApi) {
        globalThis.api = Object.freeze({
          request(opts) {
            return new Promise((resolve, reject) => {
              const id = ++nextId;
              pending.set(id, { resolve, reject });
              send(JSON.stringify({ id, opts }));
            });
          },
        });
      }
      const deliver = (json) => {
        const m = JSON.parse(json);
        const p = pending.get(m.id);
        if (!p) return;
        pending.delete(m.id);
        if (m.error) p.reject(new Error(m.error)); else p.resolve(m.response);
      };
      const getLogs = () => JSON.stringify(logs);
      delete globalThis.__send; delete globalThis.__specJson; delete globalThis.__allowApi;
      return { deliver, getLogs };
    })()
  `;
  const bridge = vm.runInContext(bootstrap, context);
  deliver = bridge.deliver;

  const wrapped = `
    (async () => {
      const __fn = (${code});
      if (typeof __fn !== 'function') throw new TypeError('code must be an async arrow function');
      const __r = await __fn();
      return JSON.stringify(__r === undefined ? null : __r);
    })()
  `;

  try {
    const resultJson = await vm.runInContext(wrapped, context, { timeout: 2000, filename: 'model-code.js' });
    out({ type: 'done', ok: true, resultJson, logsJson: bridge.getLogs() });
  } catch (err) {
    out({ type: 'done', ok: false, error: String(err?.message ?? err), logsJson: bridge.getLogs() });
  }
}
