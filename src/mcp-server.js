// Code Mode MCP server (stdio). Exposes the whole fake API through two tools:
//   search  - model writes JS that filters the OpenAPI spec
//   execute - model writes JS that calls the API via api.request()
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { createApi } from './api.js';
import { buildSpec } from './spec.js';
import { runInSandbox } from './sandbox.js';

// The secret lives only in this process. Sandboxed code never sees it.
const TOKEN = process.env.ACME_TOKEN || 'demo-secret-token';
const api = createApi({ token: TOKEN });
const specJson = JSON.stringify(buildSpec());

export const SEARCH_DESCRIPTION = `Search the Acme Work API OpenAPI spec by writing JavaScript.
\`code\` is an async arrow function. A global \`spec\` holds the full OpenAPI 3.1 document; all schemas are inlined (no $refs).
Return only what you need. Example:
async () => Object.entries(spec.paths).filter(([p]) => p.includes('tasks')).map(([p, ops]) => ({ p, methods: Object.keys(ops) }))`;

export const EXECUTE_DESCRIPTION = `Call the Acme Work API by writing JavaScript.
\`code\` is an async arrow function. A global \`api\` is available:
  api.request({ method: 'GET'|'POST'|'PATCH'|'DELETE', path: string, query?: object, body?: object }): Promise<{ status: number, body: any }>
Paths start with /v1 (see the search tool). Auth is added for you. Lists are paginated: { items, page, per_page, total, total_pages }.
Chain calls, loop over pages and return a small result. No fetch, fs, process or require. 5 s limit, 100 requests per run.`;

function toToolResult(out) {
  const meta = `\n\n[sandbox: ${out.ms} ms, ${out.requests} API requests]`;
  const logs = out.logs?.length ? `\n[console]\n${out.logs.join('\n')}` : '';
  if (!out.ok) return { isError: true, content: [{ type: 'text', text: `Error: ${out.error}${logs}${meta}` }] };
  return { content: [{ type: 'text', text: out.result + logs + meta }] };
}

const server = new McpServer({ name: 'acme-code-mode', version: '0.1.0' });

server.registerTool(
  'search',
  { description: SEARCH_DESCRIPTION, inputSchema: { code: z.string().describe('async arrow function') } },
  async ({ code }) => toToolResult(await runInSandbox({ code, specJson })),
);

server.registerTool(
  'execute',
  { description: EXECUTE_DESCRIPTION, inputSchema: { code: z.string().describe('async arrow function') } },
  async ({ code }) =>
    toToolResult(
      await runInSandbox({
        code,
        onRequest: async ({ method = 'GET', path, query, body } = {}) => {
          if (typeof path !== 'string') throw new Error('path must be a string');
          return api.handle({ method, path, query, body, headers: { authorization: `Bearer ${TOKEN}` } });
        },
      }),
    ),
);

await server.connect(new StdioServerTransport());
console.error('acme-code-mode MCP server ready (stdio)');
