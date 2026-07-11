"""Calendar endpoint - Google Calendar integration."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth import token_manager
from app.errors import parse_google_error

router = APIRouter()

GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
DEFAULT_TIMEZONE = "America/New_York"
_MAX_PAGES = 20


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
    """Fetch events from Google Calendar API, following pagination until exhausted."""
    base_params = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 250,
    }

    items: list[dict] = []
    page_token: str | None = None

    async with httpx.AsyncClient() as client:
        for _ in range(_MAX_PAGES):
            params = dict(base_params)
            if page_token:
                params["pageToken"] = page_token

            response = await token_manager.google_request(
                client, "GET", f"{GOOGLE_CALENDAR_API}/calendars/primary/events", params=params
            )

            if response.status_code != 200:
                raise HTTPException(
                    502, f"Google Calendar API error: {parse_google_error(response.text)}"
                )

            data = response.json()
            items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    events = []

    for item in items:
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
    start: str                      # ISO 8601 datetime or date
    end: str                        # ISO 8601 datetime or date
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    timezone: str = DEFAULT_TIMEZONE
    recurrence: list[str] | None = None   # RRULE values, e.g. ["FREQ=WEEKLY;BYDAY=MO"]
    reminder_minutes: list[int] | None = None  # e.g. [10, 60] for 10 min and 1 hr before


class UpdateEventRequest(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    description: str | None = None
    timezone: str = DEFAULT_TIMEZONE
    recurrence: list[str] | None = None
    reminder_minutes: list[int] | None = None


@router.post("/events", response_model=CalendarEvent, status_code=201)
async def create_event(body: CreateEventRequest):
    """Create a new calendar event."""
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
    if body.recurrence:
        payload["recurrence"] = [
            r if r.startswith("RRULE:") else f"RRULE:{r}"
            for r in body.recurrence
        ]
    if body.reminder_minutes is not None:
        payload["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in body.reminder_minutes],
        }

    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "POST", f"{GOOGLE_CALENDAR_API}/calendars/primary/events", json=payload
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google Calendar API error: {parse_google_error(response.text)}")

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
    if body.recurrence is not None:
        payload["recurrence"] = [
            r if r.startswith("RRULE:") else f"RRULE:{r}"
            for r in body.recurrence
        ]
    if body.reminder_minutes is not None:
        payload["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in body.reminder_minutes],
        }

    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client,
            "PATCH",
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events/{event_id}",
            json=payload,
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Calendar API error: {parse_google_error(response.text)}")

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


@router.get("/events/search", response_model=CalendarResponse)
async def search_events(
    q: str = Query(..., description="Keyword to search in event titles, descriptions, and locations."),
    max_results: int = Query(default=10, ge=1, le=50),
):
    """Search calendar events by keyword across all time."""
    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client,
            "GET",
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
            params={"q": q, "singleEvents": "true", "maxResults": max_results},
        )
    if response.status_code != 200:
        raise HTTPException(502, f"Google Calendar API error: {parse_google_error(response.text)}")
    events = []
    for item in response.json().get("items", []):
        start = item.get("start", {})
        end = item.get("end", {})
        all_day = "date" in start
        events.append(CalendarEvent(
            id=item.get("id", ""),
            title=item.get("summary", "(No title)"),
            start=start.get("date") or start.get("dateTime", ""),
            end=end.get("date") or end.get("dateTime", ""),
            all_day=all_day,
            location=item.get("location"),
        ))
    return CalendarResponse(events=events, count=len(events))


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: str):
    """Delete a calendar event."""
    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "DELETE", f"{GOOGLE_CALENDAR_API}/calendars/primary/events/{event_id}"
        )

    if response.status_code not in (200, 204):
        raise HTTPException(502, f"Google Calendar API error: {parse_google_error(response.text)}")


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

    payload = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": "primary"}],
    }

    async with httpx.AsyncClient() as client:
        response = await token_manager.google_request(
            client, "POST", f"{GOOGLE_CALENDAR_API}/freeBusy", json=payload
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Google Calendar API error: {parse_google_error(response.text)}")

    data = response.json()
    busy_raw = data.get("calendars", {}).get("primary", {}).get("busy", [])

    return AvailabilityResponse(
        time_min=start.isoformat(),
        time_max=end.isoformat(),
        busy=[BusySlot(start=s["start"], end=s["end"]) for s in busy_raw],
    )
