"""Shared error-parsing utilities for external API integrations."""

import json


def parse_google_error(response_text: str) -> str:
    """Extract a readable message from a Google API error response.

    Google APIs return JSON like {"error": {"code": 400, "message": "...", "status": "..."}}.
    Returns "STATUS: message" when parseable, raw text otherwise.
    """
    try:
        body = json.loads(response_text)
        err = body.get("error", {})
        msg = err.get("message", "")
        status = err.get("status", "")
        if msg:
            return f"{status}: {msg}" if status else msg
    except Exception:
        pass
    return response_text
