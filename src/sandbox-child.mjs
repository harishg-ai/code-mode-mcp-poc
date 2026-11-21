// Runs inside a separate Node process started with --permission (no fs
// writes, no child processes, no workers) and an empty environment.
// Inside that process, model code runs in a vm context that holds no
// host-realm objects: everything it can reach was created inside the context.
import vm from 'node:vm';

process.once('message', async (msg) => {
  const { code, specJson, allowApi } = msg;

  const context = vm.createContext(Object.create(null), {
    codeGeneration: { strings: false, wasm: false }, // no eval / new Function
  });

  // Host-side bridge functions. They are installed under temporary names,
  // captured into closures by the bootstrap script, then deleted, so model
  // code never holds a reference to a host-realm function.
  context.__send = (json) => process.send({ type: 'request', payload: json });
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
  const { deliver, getLogs } = vm.runInContext(bootstrap, context);

  process.on('message', (m) => {
    if (m.type === 'response') deliver(m.payload);
  });

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
    process.send({ type: 'done', ok: true, resultJson, logsJson: getLogs() });
  } catch (err) {
    process.send({ type: 'done', ok: false, error: String(err?.message ?? err), logsJson: getLogs() });
  }
});
