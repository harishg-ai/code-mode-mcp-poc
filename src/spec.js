// Builds an OpenAPI 3.1 spec for the fake API, with every schema inlined
// (no $refs), the same shape the article's `search` tool exposes.
import { RESOURCES } from './api.js';

function fieldSchema(type) {
  if (Array.isArray(type)) return { type: 'string', enum: type };
  if (type.startsWith('ref:')) return { type: 'integer', description: `id of a ${type.slice(4)} row` };
  if (type === 'date') return { type: 'string', format: 'date' };
  if (type === 'number') return { type: 'number' };
  if (type === 'boolean') return { type: 'boolean' };
  return { type: 'string' };
}

function objectSchema(fields, withId = true) {
  const properties = withId ? { id: { type: 'integer' } } : {};
  for (const [k, t] of Object.entries(fields)) properties[k] = fieldSchema(t);
  return { type: 'object', properties };
}

const idParam = { name: 'id', in: 'path', required: true, schema: { type: 'integer' } };
const pageParams = [
  { name: 'page', in: 'query', schema: { type: 'integer', minimum: 1 } },
  { name: 'per_page', in: 'query', schema: { type: 'integer', minimum: 1, maximum: 50 } },
];

function listResponse(fields) {
  return {
    200: {
      description: 'Paginated list',
      content: {
        'application/json': {
          schema: {
            type: 'object',
            properties: {
              items: { type: 'array', items: objectSchema(fields) },
              page: { type: 'integer' },
              per_page: { type: 'integer' },
              total: { type: 'integer' },
              total_pages: { type: 'integer' },
            },
          },
        },
      },
    },
  };
}

export function buildSpec() {
  const paths = {};
  for (const [r, fields] of Object.entries(RESOURCES)) {
    const tag = r;
    const one = { 200: { description: 'OK', content: { 'application/json': { schema: objectSchema(fields) } } } };
    const filterParams = Object.entries(fields).map(([k, t]) => ({
      name: k,
      in: 'query',
      description: `Exact-match filter on ${k}`,
      schema: fieldSchema(t),
    }));
    paths[`/v1/${r}`] = {
      get: { operationId: `list_${r}`, tags: [tag], summary: `List ${r}`, parameters: [...pageParams, ...filterParams], responses: listResponse(fields) },
      post: {
        operationId: `create_${r}`,
        tags: [tag],
        summary: `Create a ${r} row`,
        requestBody: { required: true, content: { 'application/json': { schema: objectSchema(fields, false) } } },
        responses: { 201: one[200] },
      },
    };
    paths[`/v1/${r}/{id}`] = {
      get: { operationId: `get_${r}`, tags: [tag], summary: `Get one ${r} row`, parameters: [idParam], responses: one },
      patch: {
        operationId: `update_${r}`,
        tags: [tag],
        summary: `Update a ${r} row`,
        parameters: [idParam],
        requestBody: { required: true, content: { 'application/json': { schema: objectSchema(fields, false) } } },
        responses: one,
      },
      delete: { operationId: `delete_${r}`, tags: [tag], summary: `Delete a ${r} row`, parameters: [idParam], responses: { 204: { description: 'Deleted' } } },
    };
  }
  paths['/v1/projects/{id}/tasks'] = {
    get: {
      operationId: 'list_project_tasks',
      tags: ['projects', 'tasks'],
      summary: 'List tasks in a project',
      parameters: [idParam, ...pageParams],
      responses: listResponse(RESOURCES.tasks),
    },
  };
  paths['/v1/tasks/{id}/assign'] = {
    post: {
      operationId: 'assign_task',
      tags: ['tasks'],
      summary: 'Assign a task to a user',
      parameters: [idParam],
      requestBody: {
        required: true,
        content: { 'application/json': { schema: { type: 'object', properties: { assignee_id: { type: 'integer' } } } } },
      },
      responses: { 200: { description: 'OK', content: { 'application/json': { schema: objectSchema(RESOURCES.tasks) } } } },
    },
  };
  return {
    openapi: '3.1.0',
    info: { title: 'Acme Work API (fake)', version: '1.0.0' },
    servers: [{ url: 'https://api.acme.local' }],
    paths,
  };
}
