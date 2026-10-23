import base64
import binascii
import html
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from enum import IntEnum

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app import activity
from app.config import settings
from app.http_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class Priority(IntEnum):
    LOWEST = -2      # No notification, just badge
    LOW = -1         # Quiet notification
    NORMAL = 0       # Normal notification
    HIGH = 1         # Bypass quiet hours
    EMERGENCY = 2    # Requires acknowledgment


# Anything that doesn't fit a notification (a long document, a file Pushover can't attach) is
# stored for ITEM_TTL and the notification links to a page for it.
ITEM_TTL = timedelta(days=30)
DOCUMENT_MAX = 200_000
FILE_MAX = 10 * 1024 * 1024
PUSHOVER_ATTACHMENT_MAX = 5_242_880
# Pushover attaches only images. Only these are also shown inline on an item page; every other
# file is served as a download, so an uploaded HTML or SVG file can never run script on this
# origin (Caddy reaches it with the gateway key injected).
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class NotificationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=250)
    message: str = Field(..., min_length=1, max_length=1024)
    priority: Priority = Priority.NORMAL
    url: str | None = Field(None, max_length=512)
    url_title: str | None = Field(None, max_length=100)
    document: str | None = Field(None, max_length=DOCUMENT_MAX)
    attachment_base64: str | None = Field(None, max_length=(FILE_MAX * 4) // 3 + 8)
    attachment_type: str | None = Field(None, max_length=100)
    attachment_name: str | None = Field(None, max_length=200)


class NotificationResponse(BaseModel):
    status: int
    request: str
    link: str | None = None  # item page, when something was stored


# Notifications, not reports: a title has to fit one line on a phone, and the body a glance.
# Links belong in `url` (a tappable button), not pasted into the text.
TITLE_MAX = 48
MESSAGE_MAX = 300


def _clamp(text: str, limit: int) -> str:
    """Shorten to `limit` characters at a word boundary, with an ellipsis."""
    # Single-line text gets its whitespace tidied; multi-line bodies keep their line breaks.
    text = text.strip() if "\n" in text else " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


async def send_pushover(
    title: str,
    message: str,
    priority: Priority = Priority.NORMAL,
    url: str | None = None,
    url_title: str | None = None,
    attachment: tuple[str, bytes, str] | None = None,
) -> dict:
    """Send one Pushover message. Also used by app.alerts for the gateway's own alerts.

    `attachment` is (filename, bytes, content type): an image of at most 5 MB.
    """
    if not settings.pushover_user_key or not settings.pushover_api_token:
        raise HTTPException(
            status_code=503,
            detail="Pushover credentials not configured"
        )

    payload = {
        "token": settings.pushover_api_token,
        "user": settings.pushover_user_key,
        "title": _clamp(title, TITLE_MAX),
        "message": _clamp(message, MESSAGE_MAX) or "(no details)",
        "priority": int(priority),
    }

    if url:
        payload["url"] = url
    if url_title:
        payload["url_title"] = _clamp(url_title, 40)

    if priority == Priority.EMERGENCY:
        payload["retry"] = 60       # Retry every 60 seconds
        payload["expire"] = 3600

    files = {"attachment": attachment} if attachment else None
    response = await get_client().post(PUSHOVER_API_URL, data=payload, files=files)
    detail = {
        "message": payload["message"],
        "priority": int(priority),
        "url": url,
        "attachment": attachment[0] if attachment else None,
    }

    if response.status_code != 200:
        activity.record("notify", payload["title"], "error", detail | {"error": response.text})
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Pushover API error: {response.text}"
        )

    data = response.json()
    if data.get("status") != 1:
        activity.record("notify", payload["title"], "error", detail | {"error": str(data)})
        raise HTTPException(
            status_code=400,
            detail=f"Pushover rejected: {data.get('errors', 'Unknown error')}"
        )
    activity.record("notify", payload["title"], "ok", detail)
    return data


# ---------------------------------------------------------------- stored items

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL)")
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
    return _pool


def item_url(item_id: uuid.UUID) -> str:
    return f"{settings.console_url.rstrip('/')}/gw/notify/items/{item_id}"


async def _store_item(
    title: str,
    body: str | None,
    link: str | None,
    file: tuple[str, bytes, str] | None,
) -> uuid.UUID:
    pool = await _get_pool()
    item_id = uuid.uuid4()
    name, data, content_type = file if file else (None, None, None)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM phone_items WHERE expires_at < NOW()")
        await conn.execute(
            "INSERT INTO phone_items "
            "(id, expires_at, title, body, link, file_name, content_type, file_data) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            item_id, datetime.now(UTC) + ITEM_TTL, title, body, link, name, content_type, data,
        )
    return item_id


async def deliver(
    title: str,
    message: str,
    priority: Priority = Priority.NORMAL,
    url: str | None = None,
    url_title: str | None = None,
    document: str | None = None,
    file: tuple[str, bytes, str] | None = None,
) -> dict:
    """Send to the phone: a plain notification, or one that links to a stored item.

    Images Pushover can take ride along as the attachment. A document, or a file Pushover can't
    attach, is stored and the notification's button opens its page; the caller's own `url` is
    kept as a button on that page. Shared by REST and the MCP `send_to_phone` tool.
    """
    attachment = None
    if file and file[2] in IMAGE_TYPES and len(file[1]) <= PUSHOVER_ATTACHMENT_MAX:
        attachment, file = file, None

    link = None
    if document or file:
        item_id = await _store_item(title, document, url, file)
        link = item_url(item_id)
        url, url_title = link, "Open"

    data = await send_pushover(title, message, priority, url, url_title, attachment)
    return {"status": data["status"], "request": data["request"], "link": link}


def _decode_attachment(body: NotificationRequest) -> tuple[str, bytes, str] | None:
    if not body.attachment_base64:
        return None
    if not body.attachment_type:
        raise HTTPException(422, "attachment_type is required with attachment_base64")
    try:
        data = base64.b64decode(body.attachment_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(422, "attachment_base64 is not valid base64") from e
    if len(data) > FILE_MAX:
        raise HTTPException(413, f"Attachment over {FILE_MAX // (1024 * 1024)} MB")
    return body.attachment_name or "attachment", data, body.attachment_type


@router.post("", response_model=NotificationResponse)
async def send_notification(notification: NotificationRequest):
    return await deliver(
        notification.title,
        notification.message,
        notification.priority,
        notification.url,
        notification.url_title,
        notification.document,
        _decode_attachment(notification),
    )


@router.post("/file", response_model=NotificationResponse)
async def send_file(
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=250),
    message: str = Form("", max_length=1024),
    priority: Priority = Form(Priority.NORMAL),
    url: str | None = Form(None, max_length=512),
    url_title: str | None = Form(None, max_length=100),
):
    """Multipart upload, for sending a local file: `curl -F title=... -F file=@path ...`."""
    data = await file.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        raise HTTPException(413, f"File over {FILE_MAX // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "File is empty")
    name = file.filename or "file"
    content_type = file.content_type or "application/octet-stream"
    return await deliver(
        title, message or name, priority, url, url_title, None, (name, data, content_type)
    )


# ---------------------------------------------------------------- item pages

_URL_RE = re.compile(r"https?://[^\s<>\"']+")

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --bg: #fafaf7; --fg: #1f1f1c; --muted: #6b6a64;
  --line: #deddd5; --accent: #185fa5; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #1c1c1a; --fg: #ecebe4;
  --muted: #a3a29b; --line: #3a3a36; --accent: #85b7eb; }} }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 -apple-system, system-ui, sans-serif; }}
main {{ max-width: 720px; margin: 0 auto; padding: 20px 16px 48px; }}
h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 4px; }}
.meta {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
.body {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
a {{ color: var(--accent); }}
.button {{ display: inline-block; margin: 0 8px 16px 0; padding: 8px 14px;
  border: 1px solid var(--line); border-radius: 8px; text-decoration: none; }}
img {{ max-width: 100%; border-radius: 8px; border: 1px solid var(--line); }}
</style></head>
<body><main>
<h1>{title}</h1>
<div class="meta">Sent {sent} · kept until {expires}</div>
{actions}{image}<div class="body">{body}</div>
</main></body></html>
"""


def _linkify(text: str) -> str:
    """Escape text and turn bare http(s) URLs into links."""
    out, last = [], 0
    for m in _URL_RE.finditer(text):
        out.append(html.escape(text[last : m.start()]))
        href = html.escape(m.group(0), quote=True)
        out.append(f'<a href="{href}" rel="noreferrer">{href}</a>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


async def _get_item(item_id: uuid.UUID, with_file: bool) -> asyncpg.Record:
    pool = await _get_pool()
    columns = "*" if with_file else (
        "id, created_at, expires_at, title, body, link, file_name, content_type"
    )
    row = await pool.fetchrow(
        f"SELECT {columns} FROM phone_items WHERE id = $1 AND expires_at > NOW()", item_id
    )
    if row is None:
        raise HTTPException(404, "Not found, or older than 30 days")
    return row


@router.get("/items/{item_id}", response_class=HTMLResponse)
async def item_page(item_id: uuid.UUID):
    row = await _get_item(item_id, with_file=False)
    file_href = f"{item_id}/file"
    actions = ""
    if row["link"]:
        actions += (
            f'<a class="button" href="{html.escape(row["link"], quote=True)}" '
            f'rel="noreferrer">Open link</a>'
        )
    if row["file_name"]:
        name = html.escape(row["file_name"])
        actions += f'<a class="button" href="{file_href}">Download {name}</a>'
    image = (
        f'<p><img src="{file_href}" alt="{html.escape(row["file_name"])}"></p>'
        if row["content_type"] in IMAGE_TYPES
        else ""
    )
    page = _PAGE.format(
        title=html.escape(row["title"]),
        sent=row["created_at"].strftime("%Y-%m-%d %H:%M UTC"),
        expires=row["expires_at"].strftime("%Y-%m-%d"),
        actions=f"<p>{actions}</p>" if actions else "",
        image=image,
        body=_linkify(row["body"] or ""),
    )
    return HTMLResponse(
        page,
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'"
            ),
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/items/{item_id}/file")
async def item_file(item_id: uuid.UUID):
    row = await _get_item(item_id, with_file=True)
    if row["file_data"] is None:
        raise HTTPException(404, "This item has no file")
    inline = row["content_type"] in IMAGE_TYPES
    name = re.sub(r"[^\w.\- ]", "_", row["file_name"] or "file")
    return Response(
        row["file_data"],
        media_type=row["content_type"] if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
