// A fake "Acme Work" REST API: in-memory, deterministic seed data.
// It stands in for a large real API (Cloudflare's has 2,500+ endpoints).
// Every resource gets list/create/get/update/delete, plus a few extra routes.

export const RESOURCES = {
  projects: { name: 'string', status: ['active', 'archived'], owner_id: 'ref:users' },
  tasks: {
    title: 'string',
    status: ['todo', 'in_progress', 'done'],
    priority: ['low', 'medium', 'high'],
    project_id: 'ref:projects',
    assignee_id: 'ref:users',
    due_date: 'date',
  },
  users: { name: 'string', email: 'string', role: ['admin', 'member', 'guest'], team_id: 'ref:teams' },
  teams: { name: 'string' },
  comments: { body: 'string', task_id: 'ref:tasks', author_id: 'ref:users' },
  labels: { name: 'string', color: 'string' },
  milestones: { name: 'string', project_id: 'ref:projects', due_date: 'date' },
  customers: { name: 'string', tier: ['free', 'pro', 'enterprise'], mrr: 'number' },
  invoices: { customer_id: 'ref:customers', amount: 'number', status: ['draft', 'sent', 'paid', 'overdue'] },
  tickets: { subject: 'string', customer_id: 'ref:customers', severity: ['sev1', 'sev2', 'sev3'], status: ['open', 'closed'] },
  releases: { version: 'string', project_id: 'ref:projects', channel: ['stable', 'beta'] },
  webhooks: { url: 'string', event: ['task.created', 'task.done', 'invoice.paid'], active: 'boolean' },
  dns_records: { zone: 'string', type: ['A', 'AAAA', 'CNAME', 'TXT', 'MX'], name: 'string', content: 'string', ttl: 'number' },
  firewall_rules: { zone: 'string', expression: 'string', action: ['block', 'challenge', 'allow', 'log'], enabled: 'boolean' },
  rate_limits: { zone: 'string', path: 'string', requests_per_minute: 'number', action: ['block', 'challenge'] },
  audit_logs: { actor_id: 'ref:users', action: 'string', resource: 'string' },
};

const SEED_COUNT = { tasks: 230, users: 18, teams: 4, projects: 9, comments: 120, customers: 40, invoices: 90, tickets: 60 };

// Small deterministic PRNG so every run sees the same data.
function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

function seed() {
  const rand = rng(42);
  const pick = (arr) => arr[Math.floor(rand() * arr.length)];
  const db = {};
  for (const r of Object.keys(RESOURCES)) db[r] = [];
  // Seed referenced resources first so refs point at real ids.
  const order = ['teams', 'users', 'projects', 'customers', ...Object.keys(RESOURCES).filter(
    (r) => !['teams', 'users', 'projects', 'customers'].includes(r),
  )];
  for (const r of order) {
    const n = SEED_COUNT[r] ?? 12;
    for (let i = 1; i <= n; i++) {
      const row = { id: i };
      for (const [field, type] of Object.entries(RESOURCES[r])) {
        if (Array.isArray(type)) row[field] = pick(type);
        else if (type.startsWith('ref:')) {
          const target = db[type.slice(4)];
          row[field] = target.length ? pick(target).id : null;
        } else if (type === 'date') {
          const d = new Date(Date.UTC(2026, 9, 1) + Math.floor(rand() * 120) * 86400000);
          row[field] = d.toISOString().slice(0, 10);
        } else if (type === 'number') row[field] = Math.round(rand() * 5000);
        else if (type === 'boolean') row[field] = rand() > 0.3;
        else row[field] = `${r.slice(0, -1)} ${field} ${i}`;
      }
      db[r].push(row);
    }
  }
  return db;
}

export function createApi({ token }) {
  const db = seed();
  let calls = 0;

  function json(status, body) {
    return { status, body };
  }

  // Router. `req` = { method, path, query, body, headers }.
  function handle(req) {
    calls++;
    if (req.headers?.authorization !== `Bearer ${token}`) return json(401, { error: 'unauthorized' });
    const method = (req.method || 'GET').toUpperCase();
    const url = new URL(req.path, 'http://acme.local');
    const query = { ...Object.fromEntries(url.searchParams), ...(req.query || {}) };
    const parts = url.pathname.split('/').filter(Boolean);
    if (parts[0] !== 'v1') return json(404, { error: 'not found' });
    const [, resource, id, sub] = parts;
    const table = db[resource];
    if (!table) return json(404, { error: `unknown resource ${resource}` });

    // Extra routes.
    if (resource === 'projects' && id && sub === 'tasks' && method === 'GET') {
      return list(db.tasks.filter((t) => t.project_id === Number(id)), query);
    }
    if (resource === 'tasks' && id && sub === 'assign' && method === 'POST') {
      const t = table.find((x) => x.id === Number(id));
      if (!t) return json(404, { error: 'not found' });
      t.assignee_id = req.body?.assignee_id ?? null;
      return json(200, t);
    }
    if (sub) return json(404, { error: 'not found' });

    if (!id) {
      if (method === 'GET') return list(table, query);
      if (method === 'POST') {
        const row = { id: table.length ? Math.max(...table.map((x) => x.id)) + 1 : 1, ...req.body };
        table.push(row);
        return json(201, row);
      }
      return json(405, { error: 'method not allowed' });
    }
    const idx = table.findIndex((x) => x.id === Number(id));
    if (idx < 0) return json(404, { error: 'not found' });
    if (method === 'GET') return json(200, table[idx]);
    if (method === 'PATCH') return json(200, Object.assign(table[idx], req.body, { id: table[idx].id }));
    if (method === 'DELETE') {
      table.splice(idx, 1);
      return json(204, null);
    }
    return json(405, { error: 'method not allowed' });
  }

  // Paginated list with exact-match filters on any field: ?status=done&page=2
  function list(rows, query) {
    const page = Math.max(1, Number(query.page) || 1);
    const perPage = Math.min(50, Math.max(1, Number(query.per_page) || 20));
    const filters = Object.entries(query).filter(([k]) => !['page', 'per_page'].includes(k));
    const filtered = rows.filter((r) => filters.every(([k, v]) => String(r[k]) === String(v)));
    const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
    return json(200, {
      items: filtered.slice((page - 1) * perPage, page * perPage),
      page,
      per_page: perPage,
      total: filtered.length,
      total_pages: totalPages,
    });
  }

  return { handle, stats: () => ({ calls }) };
}
