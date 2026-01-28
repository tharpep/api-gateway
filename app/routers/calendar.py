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

