"""Tasks endpoint - Google Tasks integration."""

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth import token_manager
from app.errors import parse_google_error

router = APIRouter()

GOOGLE_TASKS_API = "https://tasks.googleapis.com/tasks/v1"
_MAX_PAGES = 20


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
    """Fetch all task lists from Google Tasks API, following pagination until exhausted."""
    items: list[dict] = []
    page_token: str | None = None

    async with httpx.AsyncClient() as client:
        for _ in range(_MAX_PAGES):
            params = {"maxResults": 100}
            if page_token:
                params["pageToken"] = page_token

            response = await token_manager.google_request(
                client, "GET", f"{GOOGLE_TASKS_API}/users/@me/lists", params=params
            )

            if response.status_code != 200:
                raise HTTPException(
                    502, f"Google Tasks API error: {parse_google_error(response.text)}"
                )

            data = response.json()
            items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    return [TaskList(id=item["id"], title=item["title"]) for item in items]


async def _fetch_tasks_from_list(list_id: str, list_name: str, include_completed: bool = False) -> list[Task]:
    """Fetch tasks from a specific list, following pagination until exhausted."""
    items: list[dict] = []
    page_token: str | None = None

    async with httpx.AsyncClient() as client:
        for _ in range(_MAX_PAGES):
            params = {
                "showCompleted": "true" if include_completed else "false",
                "showHidden": "false",
                "maxResults": 100,
            }
            if page_token:
                params["pageToken"] = page_token

            response = await token_manager.google_request(
                client, "GET", f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks", params=params
            )

            if response.status_code != 200:
                return []

            data = response.json()
            items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    return [
        Task(
            id=item["id"],
            title=item.get("title", "(No title)"),
            status=item.get("status", "needsAction"),
            due=item.get("due"),
            notes=item.get("notes"),
            list_name=list_name,
        )
        for item in items
    ]


@router.get("/lists")
async def get_task_lists():
    """Get all task lists."""
    task_lists = await _fetch_task_lists()
    return {"lists": task_lists, "count": len(task_lists)}


async def _fetch_task_list_title(list_id: str) -> str:
    """Fetch a task list's title by ID. Falls back to list_id on failure."""
    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "GET", f"{GOOGLE_TASKS_API}/users/@me/lists/{list_id}"
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
    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "POST", f"{GOOGLE_TASKS_API}/users/@me/lists", json={"title": body.title}
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google Tasks API error: {parse_google_error(response.text)}")

    item = response.json()
    return TaskList(id=item["id"], title=item["title"])


@router.patch("/lists/{list_id}")
async def rename_task_list(list_id: str, body: TaskListRequest):
    """Rename an existing task list."""
    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client,
            "PATCH",
            f"{GOOGLE_TASKS_API}/users/@me/lists/{list_id}",
            json={"title": body.title},
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Tasks API error: {parse_google_error(response.text)}")

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
    payload: dict = {"title": body.title}
    if body.notes:
        payload["notes"] = body.notes
    if body.due:
        payload["due"] = body.due

    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "POST", f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks", json=payload
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google Tasks API error: {parse_google_error(response.text)}")

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
        response = await token_manager.google_request(
            client,
            "PATCH",
            f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks/{task_id}",
            json=payload,
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Tasks API error: {parse_google_error(response.text)}")

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
    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "DELETE", f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks/{task_id}"
        )

    if response.status_code not in (200, 204):
        raise HTTPException(502, f"Google Tasks API error: {parse_google_error(response.text)}")
