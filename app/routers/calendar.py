"""Calendar endpoint - Google Calendar integration."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth.google import GoogleOAuth, TokenData
from app.config import settings

router = APIRouter()

GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
DEFAULT_TIMEZONE = "America/New_York"

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


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: str
    end: str
    all_day: bool = False
    location: str | None = None


class CalendarResponse(BaseModel):
    events: list[CalendarEvent]
    count: int


async def _fetch_events(time_min: datetime, time_max: datetime) -> list[CalendarEvent]:
    """Fetch events from Google Calendar API."""
    access_token = await _get_access_token()

    params = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 50,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code == 401:
        global _cached_token
        _cached_token = None
        access_token = await _get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Calendar API error: {response.text}")

    data = response.json()
    events = []

    for item in data.get("items", []):
        start = item.get("start", {})
        end = item.get("end", {})

        if "date" in start:
            start_str = start["date"]
            end_str = end.get("date", start_str)
            all_day = True
        else:
            start_str = start.get("dateTime", "")
            end_str = end.get("dateTime", "")
            all_day = False

        events.append(
            CalendarEvent(
                id=item.get("id", ""),
                title=item.get("summary", "(No title)"),
                start=start_str,
                end=end_str,
                all_day=all_day,
                location=item.get("location"),
            )
        )

    return events


@router.get("/today", response_model=CalendarResponse)
async def get_today():
    """Get today's calendar events."""
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    events = await _fetch_events(start_of_day, end_of_day)
    return CalendarResponse(events=events, count=len(events))


@router.get("/events", response_model=CalendarResponse)
async def get_events(days: int = Query(default=7, ge=1, le=30)):
    """Get calendar events for the next N days."""
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_of_day + timedelta(days=days)

    events = await _fetch_events(start_of_day, end_date)
    return CalendarResponse(events=events, count=len(events))


class BusySlot(BaseModel):
    start: str
    end: str


class AvailabilityResponse(BaseModel):
    time_min: str
    time_max: str
    busy: list[BusySlot]


class CreateEventRequest(BaseModel):
    title: str
    start: str                  # ISO 8601 datetime or date
    end: str                    # ISO 8601 datetime or date
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    timezone: str = DEFAULT_TIMEZONE


class UpdateEventRequest(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    description: str | None = None
    timezone: str = DEFAULT_TIMEZONE


@router.post("/events", response_model=CalendarEvent, status_code=201)
async def create_event(body: CreateEventRequest):
    """Create a new calendar event."""
    access_token = await _get_access_token()

    if body.all_day:
        payload = {
            "summary": body.title,
            "start": {"date": body.start},
            "end": {"date": body.end},
        }
    else:
        payload = {
            "summary": body.title,
            "start": {"dateTime": body.start, "timeZone": body.timezone},
            "end": {"dateTime": body.end, "timeZone": body.timezone},
        }

    if body.location:
        payload["location"] = body.location
    if body.description:
        payload["description"] = body.description

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google Calendar API error: {response.text}")

    item = response.json()
    start = item.get("start", {})
    end = item.get("end", {})
    all_day = "date" in start

    return CalendarEvent(
        id=item["id"],
        title=item.get("summary", "(No title)"),
        start=start.get("date") or start.get("dateTime", ""),
        end=end.get("date") or end.get("dateTime", ""),
        all_day=all_day,
        location=item.get("location"),
    )


@router.patch("/events/{event_id}", response_model=CalendarEvent)
async def update_event(event_id: str, body: UpdateEventRequest):
    """Update an existing calendar event."""
    access_token = await _get_access_token()

    payload: dict = {}
    if body.title is not None:
        payload["summary"] = body.title
    if body.location is not None:
        payload["location"] = body.location
    if body.description is not None:
        payload["description"] = body.description
    if body.start is not None:
        payload["start"] = {"dateTime": body.start, "timeZone": body.timezone}
    if body.end is not None:
        payload["end"] = {"dateTime": body.end, "timeZone": body.timezone}

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events/{event_id}",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Calendar API error: {response.text}")

    item = response.json()
    start = item.get("start", {})
    end = item.get("end", {})
    all_day = "date" in start

    return CalendarEvent(
        id=item["id"],
        title=item.get("summary", "(No title)"),
        start=start.get("date") or start.get("dateTime", ""),
        end=end.get("date") or end.get("dateTime", ""),
        all_day=all_day,
        location=item.get("location"),
    )


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: str):
    """Delete a calendar event."""
    access_token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code not in (200, 204):
        raise HTTPException(502, f"Google Calendar API error: {response.text}")


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    date: str | None = Query(default=None, description="Start date (YYYY-MM-DD), defaults to today"),
    days: int = Query(default=1, ge=1, le=7),
):
    """Get busy time slots for the primary calendar (Google freebusy API)."""
    tz = ZoneInfo(DEFAULT_TIMEZONE)

    if date:
        start = datetime.fromisoformat(date).replace(tzinfo=tz)
    else:
        now = datetime.now(tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    end = start + timedelta(days=days)
    access_token = await _get_access_token()

    payload = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": "primary"}],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GOOGLE_CALENDAR_API}/freeBusy",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Calendar API error: {response.text}")

    data = response.json()
    busy_raw = data.get("calendars", {}).get("primary", {}).get("busy", [])

    return AvailabilityResponse(
        time_min=start.isoformat(),
        time_max=end.isoformat(),
        busy=[BusySlot(start=s["start"], end=s["end"]) for s in busy_raw],
    )

