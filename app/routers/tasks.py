"""Tasks endpoint - Google Tasks initially, extensible to Todoist/Notion."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/lists")
async def get_task_lists():
    """Get all task lists."""
    # TODO: Implement task list retrieval
    return {"status": "not implemented"}


@router.get("/lists/{list_id}/tasks")
async def get_tasks(list_id: str):
    """Get tasks from a specific list."""
    # TODO: Implement task retrieval
    return {"status": "not implemented"}


@router.post("/lists/{list_id}/tasks")
async def create_task(list_id: str):
    """Create a new task."""
    # TODO: Implement task creation
    return {"status": "not implemented"}


@router.patch("/lists/{list_id}/tasks/{task_id}")
async def update_task(list_id: str, task_id: str):
    """Update a task (mark complete, edit, etc)."""
    # TODO: Implement task update
    return {"status": "not implemented"}


@router.delete("/lists/{list_id}/tasks/{task_id}")
async def delete_task(list_id: str, task_id: str):
    """Delete a task."""
    # TODO: Implement task deletion
    return {"status": "not implemented"}
