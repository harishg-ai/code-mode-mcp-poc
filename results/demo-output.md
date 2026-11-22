# Code Mode MCP POC: run output
Run at 2026-11-21T20:37:37+00:00 on Python 3.13.17

## 1. Tool definitions in context (cl100k_base tokens)
| Server | Tools | Tokens |
|---|---|---|
| One tool per endpoint | 82 | 6,093 |
| Code Mode (search + execute) | 2 | 346 |

Reduction: 94.3%. The Code Mode cost stays flat as the API grows; the per-endpoint cost grows with every endpoint.

## 2. `search`: find task endpoints
```js
async () => Object.entries(spec.paths)
  .filter(([p]) => p.includes('tasks'))
  .flatMap(([p, ops]) => Object.entries(ops).map(([m, op]) => ({ route: m.toUpperCase() + ' ' + p, summary: op.summary,
    filters: (op.parameters || []).filter(x => x.in === 'query').map(x => x.name) })))
```
Result:
```json
[{"route":"GET /v1/tasks","summary":"List tasks","filters":["page","per_page","title","status","priority","project_id","assignee_id","due_date"]},{"route":"POST /v1/tasks","summary":"Create a tasks row","filters":[]},{"route":"GET /v1/tasks/{id}","summary":"Get one tasks row","filters":[]},{"route":"PATCH /v1/tasks/{id}","summary":"Update a tasks row","filters":[]},{"route":"DELETE /v1/tasks/{id}","summary":"Delete a tasks row","filters":[]},{"route":"GET /v1/projects/{id}/tasks","summary":"List tasks in a project","filters":["page","per_page"]},{"route":"POST /v1/tasks/{id}/assign","summary":"Assign a task to a user","filters":[]}]

[sandbox: 109 ms, 0 API requests]
```
Spec size: 12,548 tokens. Search result: 189 tokens.

## 3. `execute`: "Who has the most overdue high-priority tasks?"
```js
async () => {
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
}
```
Result:
```
{"checked":82,"overdue":23,"top":[{"name":"user name 18","email":"user email 18","overdue_high_priority":4},{"name":"user name 5","email":"user email 5","overdue_high_priority":3},{"name":"user name 6","email":"user email 6","overdue_high_priority":3}]}

[sandbox: 95 ms, 5 API requests]
```

| Approach | Model round trips | Tool-result tokens in context |
|---|---|---|
| Classic tool calls | 5 | 3,438 |
| Code Mode execute | 1 | 88 |

## 4. Sandbox probes (each should be blocked)
| Probe | Outcome |
|---|---|
| read secrets from env | ran: "no process global" |
| require a module | blocked: Error executing tool execute: Error: require is not defined |
| dynamic import | blocked: Error executing tool execute: Error: A dynamic import callback was not specified. |
| network fetch | blocked: Error executing tool execute: Error: fetch is not defined |
| eval | blocked: Error executing tool execute: Error: Code generation from strings disallowed for this context |
| Function-constructor escape | blocked: Error executing tool execute: Error: Code generation from strings disallowed for this context |
| prototype-chain escape | blocked: Error executing tool execute: Error: Code generation from strings disallowed for this context |
| infinite loop | blocked: Error executing tool execute: Error: Script execution timed out after 2000ms |
| read the bearer token | ran: {"id":1,"name":"user name 1","email":"user email 1","role":"admin","team_id":1} |

## 5. Write approval (host-side, outside the model)
```js
async () => (await api.request({ method: 'DELETE', path: '/v1/tasks/7' })).status
```
| Case | Outcome |
|---|---|
| User declines in the approval prompt | blocked: Error executing tool execute: Error: blocked: DELETE /v1/tasks/7 was not approved by the user |
| User approves | ran: status 204 |
| Loop of 3 deletes, user declines the first | ["blocked: DELETE /v1/tasks/8 was not approved by the user","blocked: DELETE /v1/tasks/9 was not approved by the user","blocked: DELETE /v1/tasks/10 w |
| Client without approval support | blocked: Error executing tool execute: Error: blocked: DELETE /v1/tasks/7 was not approved by the user |
