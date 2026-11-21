// End-to-end demo. Starts the MCP server over stdio as a real MCP client,
// measures tool-definition tokens against a one-tool-per-endpoint server,
// runs search + execute the way an agent would, and probes the sandbox.
// Writes everything to results/demo-output.md.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { getEncoding } from 'js-tiktoken';
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildSpec } from '../src/spec.js';
import { createApi } from '../src/api.js';

const enc = getEncoding('cl100k_base');
const tok = (x) => enc.encode(typeof x === 'string' ? x : JSON.stringify(x)).length;
const out = [];
const log = (s = '') => {
  console.log(s);
  out.push(s);
};

const client = new Client({ name: 'demo-client', version: '0.1.0' });
await client.connect(
  new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL('../src/mcp-server.js', import.meta.url))],
    env: { ...process.env, ACME_TOKEN: 'demo-secret-token' },
    stderr: 'ignore',
  }),
);

async function call(name, code) {
  const r = await client.callTool({ name, arguments: { code } });
  return { text: r.content.map((c) => c.text).join('\n'), isError: Boolean(r.isError) };
}

log('# Code Mode MCP POC: run output');
log(`Run at ${new Date().toISOString()} on Node ${process.version}`);

// 1. Context cost of tool definitions.
const { tools } = await client.listTools();
const codeModeTokens = tok(tools);

const spec = buildSpec();
const naiveTools = [];
for (const [p, ops] of Object.entries(spec.paths)) {
  for (const [method, op] of Object.entries(ops)) {
    const properties = {};
    const required = [];
    for (const prm of op.parameters ?? []) {
      properties[prm.name] = { ...prm.schema, ...(prm.description ? { description: prm.description } : {}) };
      if (prm.required) required.push(prm.name);
    }
    const bodySchema = op.requestBody?.content?.['application/json']?.schema;
    if (bodySchema) {
      properties.body = bodySchema;
      required.push('body');
    }
    naiveTools.push({
      name: op.operationId,
      description: `${op.summary}. ${method.toUpperCase()} ${p}`,
      inputSchema: { type: 'object', properties, required },
    });
  }
}
const naiveTokens = tok(naiveTools);
log('\n## 1. Tool definitions in context (cl100k_base tokens)');
log(`| Server | Tools | Tokens |\n|---|---|---|`);
log(`| One tool per endpoint | ${naiveTools.length} | ${naiveTokens.toLocaleString()} |`);
log(`| Code Mode (search + execute) | ${tools.length} | ${codeModeTokens.toLocaleString()} |`);
log(`\nReduction: ${(100 * (1 - codeModeTokens / naiveTokens)).toFixed(1)}%. ` +
  `The Code Mode cost stays flat as the API grows; the per-endpoint cost grows with every endpoint.`);

// 2. search: discover the right endpoints without loading the spec.
log('\n## 2. `search`: find task endpoints');
const searchCode = `async () => Object.entries(spec.paths)
  .filter(([p]) => p.includes('tasks'))
  .flatMap(([p, ops]) => Object.entries(ops).map(([m, op]) => ({ route: m.toUpperCase() + ' ' + p, summary: op.summary,
    filters: (op.parameters || []).filter(x => x.in === 'query').map(x => x.name) })))`;
log('```js\n' + searchCode + '\n```');
const s = await call('search', searchCode);
log('Result:\n```json\n' + s.text + '\n```');
log(`Spec size: ${tok(spec).toLocaleString()} tokens. Search result: ${tok(s.text).toLocaleString()} tokens.`);

// 3. execute: a multi-step question answered in one call.
log('\n## 3. `execute`: "Who has the most overdue high-priority tasks?"');
const execCode = `async () => {
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
}`;
log('```js\n' + execCode + '\n```');
const e = await call('execute', execCode);
log('Result:\n```\n' + e.text + '\n```');

// Same question with classic tool calls: every raw page and user record
// would pass through the model's context. Same seed, so same data.
const local = createApi({ token: 't' });
let rawTokens = 0;
let classicCalls = 0;
for (let page = 1; ; page++) {
  const r = local.handle({ method: 'GET', path: '/v1/tasks', query: { priority: 'high', per_page: 50, page }, headers: { authorization: 'Bearer t' } });
  rawTokens += tok(r.body);
  classicCalls++;
  if (page >= r.body.total_pages) break;
}
classicCalls += 3;
rawTokens += 3 * tok(local.handle({ method: 'GET', path: '/v1/users/1', headers: { authorization: 'Bearer t' } }).body);
const resultTokens = tok(e.text);
log(`\n| Approach | Model round trips | Tool-result tokens in context |\n|---|---|---|`);
log(`| Classic tool calls | ${classicCalls} | ${rawTokens.toLocaleString()} |`);
log(`| Code Mode execute | 1 | ${resultTokens.toLocaleString()} |`);

// 4. Sandbox probes. Each should fail.
log('\n## 4. Sandbox probes (each should be blocked)');
const probes = {
  'read secrets from env': `async () => typeof process === 'undefined' ? 'no process global' : process.env`,
  'require a module': `async () => require('fs')`,
  'dynamic import': `async () => (await import('node:fs')).readFileSync('package.json', 'utf8')`,
  'network fetch': `async () => fetch('https://example.com')`,
  'eval': `async () => eval('1+1')`,
  'Function-constructor escape': `async () => api.request.constructor('return process')().env`,
  'prototype-chain escape': `async () => ({}).constructor.constructor('return this')()`,
  'infinite loop': `async () => { while (true) {} }`,
  'read the bearer token': `async () => (await api.request({ path: '/v1/users/1' })).body`,
};
log(`| Probe | Outcome |\n|---|---|`);
for (const [name, code] of Object.entries(probes)) {
  const r = await call('execute', code);
  const first = r.text.split('\n')[0].replace(/\|/g, '\\|').slice(0, 110);
  const leaked = r.text.includes('demo-secret-token');
  log(`| ${name} | ${leaked ? 'LEAKED TOKEN' : r.isError ? 'blocked: ' + first : 'ran: ' + first} |`);
}

await client.close();
mkdirSync(new URL('../results/', import.meta.url), { recursive: true });
writeFileSync(new URL('../results/demo-output.md', import.meta.url), out.join('\n') + '\n');
console.log('\nSaved results/demo-output.md');
