"""Storage endpoint — Google Drive files."""

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
import httpx

from app.auth.google import GoogleOAuth, TokenData
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
_KB_ROOT = "Knowledge Base"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_EXPORT_MIMES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


_PDF_MIME = "application/pdf"
_GOOGLE_WORKSPACE_MIMES = set(_EXPORT_MIMES)  # Google Docs, Sheets, Slides


def _is_readable(mime: str) -> bool:
    """Return True if this file can be exported/read as text."""
    if mime in _EXPORT_MIMES:
        return True
    if mime == _PDF_MIME:
        return True
    return mime.startswith("text/") or mime in {"application/json", "application/xml"}

# Known KB subfolders: lowercase category key → Drive folder name
_KB_SUBFOLDERS: dict[str, str] = {
    "general": "General",
    "projects": "Projects",
    "purdue": "Purdue",
    "career": "Career",
    "reference": "Reference",
    "conversations": "Conversations",
}

# Module-level folder ID cache — Drive folder IDs are stable across requests
_folder_id_cache: dict[str, str] = {}

_cached_token: TokenData | None = None
_oauth = GoogleOAuth()


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if needed."""
    global _cached_token
    if _cached_token is None or _oauth.is_token_expired(_cached_token):
        if not settings.google_refresh_token:
            raise HTTPException(503, "Google refresh token not configured")
        _cached_token = await _oauth.refresh_token(settings.google_refresh_token)
    return _cached_token.access_token


async def _api_get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    """Authenticated Drive API GET — auto-retries once on 401."""
    global _cached_token
    token = await _get_access_token()
    r = await client.get(
        f"{DRIVE_API}/{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 401:
        _cached_token = None
        token = await _get_access_token()
        r = await client.get(
            f"{DRIVE_API}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Drive API error: {r.text}")
    return r.json()


async def _find_folder_id(
    client: httpx.AsyncClient, name: str, parent_id: str | None = None
) -> str:
    """Find a Drive folder by name, optionally scoped to a parent. Returns folder ID."""
    cache_key = f"{parent_id}/{name}"
    if cache_key in _folder_id_cache:
        return _folder_id_cache[cache_key]

    q = f"name = '{name}' and mimeType = '{_FOLDER_MIME}' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    data = await _api_get(client, "files", {"q": q, "fields": "files(id)", "pageSize": 1})
    files = data.get("files", [])
    if not files:
        raise HTTPException(404, f"Drive folder '{name}' not found")

    folder_id = files[0]["id"]
    _folder_id_cache[cache_key] = folder_id
    return folder_id


async def _kb_subfolder_id(client: httpx.AsyncClient, subfolder_name: str) -> str:
    """Resolve 'Knowledge Base → {subfolder_name}' folder ID (cached after first lookup)."""
    kb_id = await _find_folder_id(client, _KB_ROOT)
    return await _find_folder_id(client, subfolder_name, parent_id=kb_id)


async def _list_files_in_folder(
    client: httpx.AsyncClient,
    folder_id: str,
    category: str,
    modified_after: str | None,
) -> list[dict]:
    """BFS over a Drive folder tree, returning all non-folder files with category set.

    The category is always the top-level KB subfolder category regardless of
    how deeply nested the file is.
    """
    collected: list[dict] = []
    queue: list[str] = [folder_id]

    while queue:
        current_id = queue.pop(0)

        # Fetch non-folder files in this folder
        file_q = f"'{current_id}' in parents and trashed = false and mimeType != '{_FOLDER_MIME}'"
        if modified_after:
            file_q += f" and modifiedTime > '{modified_after}'"
        file_data = await _api_get(
            client,
            "files",
            {
                "q": file_q,
                "fields": "files(id, name, mimeType, modifiedTime, size)",
                "pageSize": 100,
                "orderBy": "modifiedTime desc",
            },
        )
        collected.extend({**f, "category": category} for f in file_data.get("files", []))

        # Fetch subfolders in this folder and enqueue them
        folder_q = f"'{current_id}' in parents and trashed = false and mimeType = '{_FOLDER_MIME}'"
        folder_data = await _api_get(
            client,
            "files",
            {
                "q": folder_q,
                "fields": "files(id)",
                "pageSize": 100,
            },
        )
        queue.extend(f["id"] for f in folder_data.get("files", []))

    return collected


async def _list_files_general(
    client: httpx.AsyncClient,
    folder_id: str | None,
    query: str | None,
    max_results: int,
) -> list[dict]:
    """List Drive files using arbitrary folder/query filters."""
    q_parts = ["trashed = false", f"mimeType != '{_FOLDER_MIME}'"]
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    if query:
        q_parts.append(f"({query})")
    data = await _api_get(
        client,
        "files",
        {
            "q": " and ".join(q_parts),
            "fields": "files(id, name, mimeType, modifiedTime, size)",
            "pageSize": max_results,
            "orderBy": "modifiedTime desc",
        },
    )
    return data.get("files", [])


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str
    modified_time: str
    size: int | None = None
    category: str = ""


class DriveFilesResponse(BaseModel):
    files: list[DriveFile]
    count: int


@router.get("/files", response_model=DriveFilesResponse)
async def list_kb_files(
    category: str | None = Query(
        default=None,
        description=f"KB subfolder to list. One of: {', '.join(_KB_SUBFOLDERS)}. "
                    "Omit to list all subfolders.",
    ),
    modified_after: str | None = Query(
        default=None,
        description="ISO 8601 timestamp — return only files modified after this time",
    ),
    folder_id: str | None = Query(
        default=None,
        description="Scope to a specific Drive folder ID. Activates general Drive search mode.",
    ),
    query: str | None = Query(
        default=None,
        description="Drive search query, e.g. 'name contains \"resume\"'.",
    ),
    max_results: int = Query(default=20, ge=1, le=50),
):
    """List Drive files. Use folder_id/query for general search, or category for KB subfolders."""
    # General Drive search mode
    if folder_id is not None or query is not None:
        async with httpx.AsyncClient() as client:
            raw = await _list_files_general(client, folder_id, query, max_results)
        files = [
            DriveFile(
                id=f["id"],
                name=f["name"],
                mime_type=f["mimeType"],
                modified_time=f["modifiedTime"],
                size=int(f["size"]) if f.get("size") else None,
            )
            for f in raw
        ]
        return DriveFilesResponse(files=files, count=len(files))

    # KB subfolder mode
    if category is not None:
        category = category.lower()
        if category not in _KB_SUBFOLDERS:
            raise HTTPException(
                400,
                f"Unknown category '{category}'. Valid: {', '.join(_KB_SUBFOLDERS)}",
            )

    raw_files: list[dict] = []

    async with httpx.AsyncClient() as client:
        if category:
            kb_folder_id = await _kb_subfolder_id(client, _KB_SUBFOLDERS[category])
            raw_files = await _list_files_in_folder(client, kb_folder_id, category, modified_after)
        else:
            for cat_key, folder_name in _KB_SUBFOLDERS.items():
                try:
                    kb_folder_id = await _kb_subfolder_id(client, folder_name)
                    kb_files = await _list_files_in_folder(client, kb_folder_id, cat_key, modified_after)
                    raw_files.extend(kb_files)
                except HTTPException as e:
                    if e.status_code == 404:
                        logger.warning(f"KB subfolder '{folder_name}' not found in Drive, skipping")
                    else:
                        raise

    files = [
        DriveFile(
            id=f["id"],
            name=f["name"],
            mime_type=f["mimeType"],
            modified_time=f["modifiedTime"],
            size=int(f["size"]) if f.get("size") else None,
            category=f["category"],
        )
        for f in raw_files
    ]

    return DriveFilesResponse(files=files, count=len(files))


class DriveFolder(BaseModel):
    id: str
    name: str
    parent_id: str | None = None


class DriveFoldersResponse(BaseModel):
    folders: list[DriveFolder]
    count: int


@router.get("/folders", response_model=DriveFoldersResponse)
async def list_folders(
    parent_id: str | None = Query(
        default=None,
        description="Scope to a specific parent folder ID.",
    ),
    query: str | None = Query(
        default=None,
        description="Drive name filter, e.g. 'name contains \"Projects\"'.",
    ),
    max_results: int = Query(default=20, ge=1, le=50),
):
    """List Drive folders, optionally scoped to a parent and/or filtered by name."""
    q_parts = [f"mimeType = '{_FOLDER_MIME}'", "trashed = false"]
    if parent_id:
        q_parts.append(f"'{parent_id}' in parents")
    if query:
        q_parts.append(f"({query})")
    async with httpx.AsyncClient() as client:
        data = await _api_get(
            client,
            "files",
            {
                "q": " and ".join(q_parts),
                "fields": "files(id, name, parents)",
                "pageSize": max_results,
                "orderBy": "name",
            },
        )
    folders = [
        DriveFolder(
            id=f["id"],
            name=f["name"],
            parent_id=(f.get("parents") or [None])[0],
        )
        for f in data.get("files", [])
    ]
    return DriveFoldersResponse(folders=folders, count=len(folders))


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: str | None = None


@router.post("/folders", status_code=201)
async def create_folder(body: CreateFolderRequest):
    """Create a new folder in Google Drive."""
    global _cached_token
    metadata: dict = {"name": body.name, "mimeType": _FOLDER_MIME}
    if body.parent_id:
        metadata["parents"] = [body.parent_id]
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DRIVE_API}/files",
            json=metadata,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            resp = await client.post(
                f"{DRIVE_API}/files",
                json=metadata,
                headers={"Authorization": f"Bearer {token}"},
            )
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Drive folder creation failed: {resp.text}")
    data = resp.json()
    return {"id": data.get("id"), "name": data.get("name", body.name)}


async def _fetch_text_content(
    client: httpx.AsyncClient, file_id: str, mime: str, name: str
) -> str:
    """Download a Drive file and return its content as a string.

    Handles Google Workspace export, PDF parsing, and raw text reads.
    """
    global _cached_token
    if mime in _EXPORT_MIMES:
        url = f"{DRIVE_API}/files/{file_id}/export"
        params: dict = {"mimeType": _EXPORT_MIMES[mime]}
    else:
        url = f"{DRIVE_API}/files/{file_id}"
        params = {"alt": "media"}

    token = await _get_access_token()
    r = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        _cached_token = None
        token = await _get_access_token()
        r = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise HTTPException(502, f"Drive download error for '{name}': {r.text}")

    if mime == _PDF_MIME:
        import io
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(r.content))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as e:
            raise HTTPException(502, f"PDF extraction failed for '{name}': {e}")
        if not text:
            raise HTTPException(422, f"No text could be extracted from '{name}'.")
        return text

    return r.content.decode("utf-8", errors="replace")


@router.get("/files/{file_id}/content")
async def get_file_content(file_id: str):
    """Download a file's text content from Drive.

    Google Docs/Sheets/Slides are exported as plain text or CSV.
    PDFs are parsed and returned as plain text.
    Response includes X-File-Name and X-File-Id headers.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        meta = await _api_get(
            client, f"files/{file_id}", {"fields": "id, name, mimeType, parents"}
        )
        mime = meta.get("mimeType", "")
        name = meta.get("name", file_id)

        if not _is_readable(mime):
            raise HTTPException(
                415,
                f"Cannot read binary file ({mime}). Only text files, PDFs, and Google Docs are supported.",
            )

        text = await _fetch_text_content(client, file_id, mime, name)

    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain",
        headers={"X-File-Name": name, "X-File-Id": file_id},
    )


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

async def _multipart_upload(metadata: dict, content: str, mime_type: str) -> dict:
    """Create a new Drive file via multipart upload."""
    global _cached_token
    boundary = uuid.uuid4().hex
    encoded = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")
    content_type = f"multipart/related; boundary={boundary}"
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            content=encoded,
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        )
        if resp.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            resp = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                content=encoded,
                headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            )
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Drive upload error: {resp.text}")
    return resp.json()


async def _media_upload(file_id: str, content: str, mime_type: str) -> dict:
    """Overwrite a Drive file's content via media upload."""
    global _cached_token
    encoded = content.encode("utf-8")
    url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.patch(
            url,
            content=encoded,
            headers={"Authorization": f"Bearer {token}", "Content-Type": mime_type},
        )
        if resp.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            resp = await client.patch(
                url,
                content=encoded,
                headers={"Authorization": f"Bearer {token}", "Content-Type": mime_type},
            )
    if resp.status_code != 200:
        raise HTTPException(502, f"Drive upload error: {resp.text}")
    return resp.json()


# ---------------------------------------------------------------------------
# Create / update / delete routes
# ---------------------------------------------------------------------------

@router.get("/files/{file_id}")
async def get_file_metadata(file_id: str):
    """Get metadata for a Drive file — name, MIME type, size, and modified time.

    Use this to identify a file type before reading or manipulating it.
    """
    async with httpx.AsyncClient() as client:
        meta = await _api_get(
            client,
            f"files/{file_id}",
            {"fields": "id, name, mimeType, modifiedTime, size, parents"},
        )
    return DriveFile(
        id=meta["id"],
        name=meta.get("name", file_id),
        mime_type=meta.get("mimeType", ""),
        modified_time=meta.get("modifiedTime", ""),
        size=int(meta["size"]) if meta.get("size") else None,
    )


class MoveFileRequest(BaseModel):
    name: str | None = None          # rename; omit to keep current name
    folder_id: str | None = None     # move to this folder; omit to keep current location


@router.patch("/files/{file_id}")
async def move_file(file_id: str, body: MoveFileRequest):
    """Rename and/or move a Drive file to a different folder."""
    global _cached_token
    if not body.name and not body.folder_id:
        raise HTTPException(400, "Provide at least one of: name, folder_id")

    token = await _get_access_token()
    params: dict = {}
    metadata: dict = {}

    if body.name:
        metadata["name"] = body.name

    if body.folder_id:
        # Fetch current parents so we can remove them when adding the new one
        async with httpx.AsyncClient() as client:
            meta = await _api_get(client, f"files/{file_id}", {"fields": "parents"})
        current_parents = ",".join(meta.get("parents", []))
        params["addParents"] = body.folder_id
        if current_parents:
            params["removeParents"] = current_parents

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.patch(
            f"{DRIVE_API}/files/{file_id}",
            json=metadata,
            params={**params, "fields": "id, name, mimeType, modifiedTime, size"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            resp = await client.patch(
                f"{DRIVE_API}/files/{file_id}",
                json=metadata,
                params={**params, "fields": "id, name, mimeType, modifiedTime, size"},
                headers={"Authorization": f"Bearer {token}"},
            )
    if resp.status_code == 404:
        raise HTTPException(404, "File not found")
    if resp.status_code != 200:
        raise HTTPException(502, f"Drive API error: {resp.text}")
    r = resp.json()
    return DriveFile(
        id=r["id"],
        name=r.get("name", file_id),
        mime_type=r.get("mimeType", ""),
        modified_time=r.get("modifiedTime", ""),
        size=int(r["size"]) if r.get("size") else None,
    )


class CreateFileRequest(BaseModel):
    name: str
    content: str
    folder_id: str | None = None
    mime_type: str = "text/plain"


class UpdateFileRequest(BaseModel):
    content: str


class AppendFileRequest(BaseModel):
    content: str
    separator: str = "\n\n"


@router.post("/files", status_code=201)
async def create_file(body: CreateFileRequest):
    """Create a new file in Google Drive.

    Pass mime_type='application/vnd.google-apps.document' to create a native
    Google Doc — Drive converts the plain-text content on ingestion.
    """
    metadata: dict = {"name": body.name}
    if body.folder_id:
        metadata["parents"] = [body.folder_id]
    if body.mime_type in _GOOGLE_WORKSPACE_MIMES:
        # Drive requires the target mimeType in metadata for Workspace types;
        # content is uploaded as plain text and converted server-side.
        metadata["mimeType"] = body.mime_type
        upload_mime = "text/plain"
    else:
        upload_mime = body.mime_type
    data = await _multipart_upload(metadata, body.content, upload_mime)
    return {"id": data.get("id"), "name": data.get("name", body.name)}


@router.post("/files/{file_id}/append")
async def append_to_file(file_id: str, body: AppendFileRequest):
    """Append text to an existing Drive file or Google Doc.

    Reads the current content, concatenates the new text (with separator),
    then writes back. Works for plain text files and Google Docs alike.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        meta = await _api_get(client, f"files/{file_id}", {"fields": "id, name, mimeType"})
        mime = meta.get("mimeType", "")
        name = meta.get("name", file_id)

        if not _is_readable(mime):
            raise HTTPException(415, f"Cannot append to binary file ({mime}).")
        if mime == _PDF_MIME:
            raise HTTPException(415, "Cannot append to a PDF.")

        current = await _fetch_text_content(client, file_id, mime, name)

    combined = current + body.separator + body.content
    await _media_upload(file_id, combined, "text/plain")
    return {"id": file_id, "name": name}


class CopyFromGithubRequest(BaseModel):
    owner: str
    repo: str
    path: str
    ref: str | None = None          # branch, tag, or SHA — defaults to default branch
    folder_id: str | None = None    # Drive destination folder
    name: str | None = None         # defaults to the filename portion of path
    mime_type: str = "text/plain"


@router.post("/files/copy-from-github", status_code=201)
async def copy_github_to_drive(body: CopyFromGithubRequest):
    """Fetch a text file from GitHub and write it directly to Drive — no agent round-trip."""
    if not settings.github_token:
        raise HTTPException(503, "GitHub token not configured")

    params = {}
    if body.ref:
        params["ref"] = body.ref

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{body.owner}/{body.repo}/contents/{body.path}",
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
        )

    if resp.status_code == 404:
        raise HTTPException(404, f"File not found in GitHub: {body.path}")
    if not resp.is_success:
        raise HTTPException(502, f"GitHub API error: {resp.text}")

    data = resp.json()
    if isinstance(data, list):
        raise HTTPException(422, f"'{body.path}' is a directory, not a file.")
    if data.get("encoding") != "base64":
        raise HTTPException(422, f"Unexpected GitHub encoding: {data.get('encoding')}")

    import base64 as _base64
    try:
        content = _base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(422, f"Could not decode file content: {e}")

    filename = body.name or body.path.split("/")[-1]
    metadata: dict = {"name": filename}
    if body.folder_id:
        metadata["parents"] = [body.folder_id]
    if body.mime_type in _GOOGLE_WORKSPACE_MIMES:
        metadata["mimeType"] = body.mime_type
        upload_mime = "text/plain"
    else:
        upload_mime = body.mime_type

    drive_data = await _multipart_upload(metadata, content, upload_mime)
    return {"id": drive_data.get("id"), "name": drive_data.get("name", filename)}


class CopyDriveFileRequest(BaseModel):
    name: str | None = None         # defaults to "Copy of {original name}"
    folder_id: str | None = None    # destination folder; defaults to same folder as original


@router.post("/files/{file_id}/copy", status_code=201)
async def copy_drive_file(file_id: str, body: CopyDriveFileRequest):
    """Copy a Drive file within Drive. Content transfer is handled by Drive internally."""
    global _cached_token
    payload: dict = {}
    if body.name:
        payload["name"] = body.name
    if body.folder_id:
        payload["parents"] = [body.folder_id]

    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{DRIVE_API}/files/{file_id}/copy",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            resp = await client.post(
                f"{DRIVE_API}/files/{file_id}/copy",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    if resp.status_code == 404:
        raise HTTPException(404, "Source file not found.")
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Drive copy error: {resp.text}")
    r = resp.json()
    return {"id": r.get("id"), "name": r.get("name")}


@router.put("/files/{file_id}")
async def update_file(file_id: str, body: UpdateFileRequest):
    """Overwrite the text content of an existing Google Drive file."""
    async with httpx.AsyncClient() as client:
        meta = await _api_get(client, f"files/{file_id}", {"fields": "id, name, mimeType"})
    mime = meta.get("mimeType", "text/plain")
    if not _is_readable(mime):
        raise HTTPException(415, f"Cannot update binary file ({mime}).")
    upload_mime = mime if mime.startswith("text/") or mime == "application/json" else "text/plain"
    data = await _media_upload(file_id, body.content, upload_mime)
    return {"id": data.get("id", file_id), "name": meta.get("name", file_id)}


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str):
    """Move a Google Drive file to trash."""
    global _cached_token
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{DRIVE_API}/files/{file_id}",
            json={"trashed": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            _cached_token = None
            token = await _get_access_token()
            resp = await client.patch(
                f"{DRIVE_API}/files/{file_id}",
                json={"trashed": True},
                headers={"Authorization": f"Bearer {token}"},
            )
    if resp.status_code == 404:
        raise HTTPException(404, "File not found")
    if resp.status_code not in (200, 204):
        raise HTTPException(502, f"Drive API error: {resp.text}")
