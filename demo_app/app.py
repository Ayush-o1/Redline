"""A tiny in-memory Task API used as Redline's local demo target.

This implements exactly the surface described in examples/taskapi.yaml --
nothing more -- so `redline full --spec examples/taskapi.yaml --demo-app` can
run a complete, deterministic, offline demonstration of the pipeline without
any external service or paid API. Request bodies are read as raw dicts
(rather than FastAPI/pydantic request models) so this app can return the
exact status codes the spec documents (e.g. 400 for a missing required field
vs. 422 for an invalid enum value) instead of FastAPI's default blanket 422.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Task API", version="1.0.0")

DEMO_API_KEY = os.environ.get("DEMO_API_KEY", "test-key")

_VALID_STATUSES = {"open", "done"}

_tasks: dict[int, dict[str, Any]] = {
    1: {"id": 1, "title": "Sample task", "status": "open"},
}
_next_id = 2


def _require_api_key(x_api_key: str | None) -> None:
    if x_api_key != DEMO_API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid API key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/tasks")
def list_tasks(status: str | None = None, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    tasks = list(_tasks.values())
    if status is not None:
        tasks = [t for t in tasks if t["status"] == status]
    return {"tasks": tasks}


@app.post("/tasks", status_code=201)
async def create_task(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    global _next_id
    body = await request.json()
    if not isinstance(body, dict) or not body.get("title"):
        raise HTTPException(status_code=400, detail="'title' is required")
    status = body.get("status", "open")
    if status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status '{status}'")

    task = {"id": _next_id, "title": body["title"], "status": status}
    _tasks[_next_id] = task
    _next_id += 1
    return task


@app.get("/tasks/{task_id}")
def get_task(task_id: int, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.put("/tasks/{task_id}")
async def update_task(
    task_id: int, request: Request, x_api_key: str | None = Header(default=None)
) -> dict:
    _require_api_key(x_api_key)
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="task not found")
    body = await request.json()
    if not isinstance(body, dict) or not body.get("title") or not body.get("status"):
        raise HTTPException(status_code=422, detail="'title' and 'status' are required")
    if body["status"] not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status '{body['status']}'")

    task = {"id": task_id, "title": body["title"], "status": body["status"]}
    _tasks[task_id] = task
    return task


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, x_api_key: str | None = Header(default=None)) -> None:
    _require_api_key(x_api_key)
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="task not found")
    del _tasks[task_id]
