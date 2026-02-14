"""Google OAuth 2.0 helpers."""

import time
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.config import settings


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy"
]

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

TASKS_SCOPES = [
    "https://www.googleapis.com/auth/tasks.readonly",
]

ALL_SCOPES = CALENDAR_SCOPES + GMAIL_SCOPES + TASKS_SCOPES


class TokenData(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_at: int
    token_type: str = "Bearer"
    scope: str | None = None


class GoogleOAuth:
    def __init__(self, scopes: list[str] | None = None, redirect_uri: str | None = None):
        self.scopes = scopes or CALENDAR_SCOPES
        self.redirect_uri = redirect_uri or settings.google_redirect_uri

    def get_auth_url(self, state: str | None = None) -> str:
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state

        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> TokenData:
        payload = {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
            response.raise_for_status()
            data = response.json()

        expires_at = int(time.time()) + data.get("expires_in", 3600)

        return TokenData(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
        )

    async def refresh_token(self, refresh_token: str) -> TokenData:
        payload = {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=payload)
            response.raise_for_status()
            data = response.json()

        expires_at = int(time.time()) + data.get("expires_in", 3600)

        return TokenData(
            access_token=data["access_token"],
            refresh_token=refresh_token,
            expires_at=expires_at,
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
        )

    def is_token_expired(self, token: TokenData, buffer_seconds: int = 60) -> bool:
        return time.time() >= (token.expires_at - buffer_seconds)
