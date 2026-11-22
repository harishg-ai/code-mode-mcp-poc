"""Builds an OpenAPI 3.1 spec for the fake API, with every schema inlined
(no $refs), the same shape the article's `search` tool exposes."""

from __future__ import annotations

from typing import Any

from .api import RESOURCES


def _field_schema(kind: Any) -> dict[str, Any]:
    if isinstance(kind, list):
        return {"type": "string", "enum": kind}
    if kind.startswith("ref:"):
        return {"type": "integer", "description": f"id of a {kind[4:]} row"}
    if kind == "date":
        return {"type": "string", "format": "date"}
    if kind == "number":
        return {"type": "number"}
    if kind == "boolean":
        return {"type": "boolean"}
    return {"type": "string"}


def _object_schema(fields: dict[str, Any], with_id: bool = True) -> dict[str, Any]:
    properties: dict[str, Any] = {"id": {"type": "integer"}} if with_id else {}
    for k, t in fields.items():
        properties[k] = _field_schema(t)
    return {"type": "object", "properties": properties}


ID_PARAM = {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
PAGE_PARAMS = [
    {"name": "page", "in": "query", "schema": {"type": "integer", "minimum": 1}},
    {"name": "per_page", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 50}},
]


def _list_response(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "200": {
            "description": "Paginated list",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "items": {"type": "array", "items": _object_schema(fields)},
                            "page": {"type": "integer"},
                            "per_page": {"type": "integer"},
                            "total": {"type": "integer"},
                            "total_pages": {"type": "integer"},
                        },
                    }
                }
            },
        }
    }


def build_spec() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for r, fields in RESOURCES.items():
        one = {"200": {"description": "OK", "content": {"application/json": {"schema": _object_schema(fields)}}}}
        filter_params = [
            {"name": k, "in": "query", "description": f"Exact-match filter on {k}", "schema": _field_schema(t)}
            for k, t in fields.items()
        ]
        body = {"required": True, "content": {"application/json": {"schema": _object_schema(fields, False)}}}
        paths[f"/v1/{r}"] = {
            "get": {"operationId": f"list_{r}", "tags": [r], "summary": f"List {r}",
                    "parameters": [*PAGE_PARAMS, *filter_params], "responses": _list_response(fields)},
            "post": {"operationId": f"create_{r}", "tags": [r], "summary": f"Create a {r} row",
                     "requestBody": body, "responses": {"201": one["200"]}},
        }
        paths[f"/v1/{r}/{{id}}"] = {
            "get": {"operationId": f"get_{r}", "tags": [r], "summary": f"Get one {r} row",
                    "parameters": [ID_PARAM], "responses": one},
            "patch": {"operationId": f"update_{r}", "tags": [r], "summary": f"Update a {r} row",
                      "parameters": [ID_PARAM], "requestBody": body, "responses": one},
            "delete": {"operationId": f"delete_{r}", "tags": [r], "summary": f"Delete a {r} row",
                       "parameters": [ID_PARAM], "responses": {"204": {"description": "Deleted"}}},
        }
    paths["/v1/projects/{id}/tasks"] = {
        "get": {"operationId": "list_project_tasks", "tags": ["projects", "tasks"], "summary": "List tasks in a project",
                "parameters": [ID_PARAM, *PAGE_PARAMS], "responses": _list_response(RESOURCES["tasks"])},
    }
    paths["/v1/tasks/{id}/assign"] = {
        "post": {
            "operationId": "assign_task",
            "tags": ["tasks"],
            "summary": "Assign a task to a user",
            "parameters": [ID_PARAM],
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"type": "object", "properties": {"assignee_id": {"type": "integer"}}}}}},
            "responses": {"200": {"description": "OK", "content": {"application/json": {
                "schema": _object_schema(RESOURCES["tasks"])}}}},
        }
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Acme Work API (fake)", "version": "1.0.0"},
        "servers": [{"url": "https://api.acme.local"}],
        "paths": paths,
    }
