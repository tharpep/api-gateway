"""Tasks endpoint - Google Tasks integration."""

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth.google import TASKS_SCOPES, GoogleOAuth, TokenData
from app.config import settings

router = APIRouter()

GOOGLE_TASKS_API = "https://tasks.googleapis.com/tasks/v1"

_cached_token: TokenData | None = None
_oauth = GoogleOAuth(scopes=TASKS_SCOPES)


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if needed."""
    global _cached_token

    if _cached_token is None or _oauth.is_token_expired(_cached_token):
        if not settings.google_refresh_token:
            raise HTTPException(503, "Google refresh token not configured")
        _cached_token = await _oauth.refresh_token(settings.google_refresh_token)

    return _cached_token.access_token


class TaskList(BaseModel):
    id: str
    title: str


class Task(BaseModel):
    id: str
    title: str
    status: str
    due: str | None = None
    notes: str | None = None
    list_name: str


class TasksResponse(BaseModel):
    tasks: list[Task]
    count: int


async def _fetch_task_lists() -> list[TaskList]:
    """Fetch all task lists from Google Tasks API."""
    access_token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GOOGLE_TASKS_API}/users/@me/lists",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code == 401:
        global _cached_token
        _cached_token = None
        access_token = await _get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GOOGLE_TASKS_API}/users/@me/lists",
                headers={"Authorization": f"Bearer {access_token}"},
            )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Tasks API error: {response.text}")

    data = response.json()
    task_lists = []

    for item in data.get("items", []):
        task_lists.append(
            TaskList(
                id=item["id"],
                title=item["title"],
            )
        )

    return task_lists


async def _fetch_tasks_from_list(list_id: str, list_name: str, include_completed: bool = False) -> list[Task]:
    """Fetch tasks from a specific list."""
    access_token = await _get_access_token()

    params = {
        "showCompleted": "true" if include_completed else "false",
        "showHidden": "false",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        return []

    data = response.json()
    tasks = []

    for item in data.get("items", []):
        tasks.append(
            Task(
                id=item["id"],
                title=item.get("title", "(No title)"),
                status=item.get("status", "needsAction"),
                due=item.get("due"),
                notes=item.get("notes"),
                list_name=list_name,
            )
        )

    return tasks


@router.get("/lists")
async def get_task_lists():
    """Get all task lists."""
    task_lists = await _fetch_task_lists()
    return {"lists": task_lists, "count": len(task_lists)}


async def _fetch_task_list_title(list_id: str) -> str:
    """Fetch a task list's title by ID. Falls back to list_id on failure."""
    access_token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GOOGLE_TASKS_API}/users/@me/lists/{list_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code == 200:
        return response.json().get("title", list_id)
    return list_id


@router.get("/lists/{list_id}/tasks")
async def get_tasks(list_id: str, include_completed: bool = Query(default=False)):
    """Get tasks from a specific list. Excludes completed tasks by default."""
    list_title = await _fetch_task_list_title(list_id)
    tasks = await _fetch_tasks_from_list(list_id, list_title, include_completed=include_completed)
    return {"tasks": tasks, "count": len(tasks)}


class TaskListRequest(BaseModel):
    title: str


@router.post("/lists", status_code=201)
async def create_task_list(body: TaskListRequest):
    """Create a new task list."""
    access_token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GOOGLE_TASKS_API}/users/@me/lists",
            json={"title": body.title},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google Tasks API error: {response.text}")

    item = response.json()
    return TaskList(id=item["id"], title=item["title"])


@router.patch("/lists/{list_id}")
async def rename_task_list(list_id: str, body: TaskListRequest):
    """Rename an existing task list."""
    access_token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{GOOGLE_TASKS_API}/users/@me/lists/{list_id}",
            json={"title": body.title},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Tasks API error: {response.text}")

    item = response.json()
    return TaskList(id=item["id"], title=item["title"])


class CreateTaskRequest(BaseModel):
    title: str
    notes: str | None = None
    due: str | None = None     # RFC 3339 timestamp, e.g. "2026-02-20T00:00:00.000Z"


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    notes: str | None = None
    due: str | None = None
    status: str | None = None  # "needsAction" or "completed"


@router.post("/lists/{list_id}/tasks", response_model=Task, status_code=201)
async def create_task(list_id: str, body: CreateTaskRequest):
    """Create a new task in a list."""
    access_token = await _get_access_token()

    payload: dict = {"title": body.title}
    if body.notes:
        payload["notes"] = body.notes
    if body.due:
        payload["due"] = body.due

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google Tasks API error: {response.text}")

    item = response.json()
    return Task(
        id=item["id"],
        title=item.get("title", "(No title)"),
        status=item.get("status", "needsAction"),
        due=item.get("due"),
        notes=item.get("notes"),
        list_name=list_id,
    )


@router.patch("/lists/{list_id}/tasks/{task_id}", response_model=Task)
async def update_task(list_id: str, task_id: str, body: UpdateTaskRequest):
    """Update a task (title, notes, due date, or mark complete)."""
    access_token = await _get_access_token()

    payload: dict = {}
    if body.title is not None:
        payload["title"] = body.title
    if body.notes is not None:
        payload["notes"] = body.notes
    if body.due is not None:
        payload["due"] = body.due
    if body.status is not None:
        payload["status"] = body.status

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks/{task_id}",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Tasks API error: {response.text}")

    item = response.json()
    return Task(
        id=item["id"],
        title=item.get("title", "(No title)"),
        status=item.get("status", "needsAction"),
        due=item.get("due"),
        notes=item.get("notes"),
        list_name=list_id,
    )


@router.delete("/lists/{list_id}/tasks/{task_id}", status_code=204)
async def delete_task(list_id: str, task_id: str):
    """Delete a task."""
    access_token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code not in (200, 204):
        raise HTTPException(502, f"Google Tasks API error: {response.text}")
