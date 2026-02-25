"""Sheets endpoint — Google Sheets API integration."""

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, model_validator

from app.auth.google import SHEETS_SCOPES, GoogleOAuth, TokenData
from app.config import settings
from app.errors import parse_google_error

router = APIRouter()

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API = "https://www.googleapis.com/drive/v3"

_cached_token: TokenData | None = None
_oauth = GoogleOAuth(scopes=SHEETS_SCOPES)


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if needed."""
    global _cached_token
    if _cached_token is None or _oauth.is_token_expired(_cached_token):
        if not settings.google_refresh_token:
            raise HTTPException(503, "Google refresh token not configured")
        _cached_token = await _oauth.refresh_token(settings.google_refresh_token)
    return _cached_token.access_token


async def _sheets_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    """Authenticated Sheets API GET — auto-retries once on 401."""
    global _cached_token
    token = await _get_access_token()
    r = await client.get(
        f"{SHEETS_API}/{path}",
        params=params or {},
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 401:
        _cached_token = None
        token = await _get_access_token()
        r = await client.get(
            f"{SHEETS_API}/{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Sheets API error: {parse_google_error(r.text)}")
    return r.json()


async def _sheets_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    json: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Authenticated Sheets API write (POST/PUT/DELETE) — auto-retries once on 401."""
    global _cached_token
    token = await _get_access_token()
    req = client.build_request(
        method,
        f"{SHEETS_API}/{path}",
        json=json,
        params=params or {},
        headers={"Authorization": f"Bearer {token}"},
    )
    r = await client.send(req)
    if r.status_code == 401:
        _cached_token = None
        token = await _get_access_token()
        req = client.build_request(
            method,
            f"{SHEETS_API}/{path}",
            json=json,
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
        )
        r = await client.send(req)
    if not r.is_success:
        raise HTTPException(502, f"Sheets API error: {parse_google_error(r.text)}")
    if r.status_code == 204 or not r.content:
        return {}
    return r.json()



class SheetTab(BaseModel):
    sheet_id: int
    title: str
    row_count: int
    column_count: int


class SpreadsheetInfo(BaseModel):
    id: str
    title: str
    sheets: list[SheetTab]


class CreateSpreadsheetRequest(BaseModel):
    title: str
    folder_id: str | None = None


class WriteValuesRequest(BaseModel):
    values: list[list[Any]]
    value_input_option: str = "USER_ENTERED"  # USER_ENTERED or RAW

    @model_validator(mode="before")
    @classmethod
    def coerce_flat_values(cls, data: Any) -> Any:
        if isinstance(data, dict):
            v = data.get("values")
            if isinstance(v, list) and v and not isinstance(v[0], list):
                return {**data, "values": [v]}
        return data


class AppendRowsRequest(BaseModel):
    values: list[list[Any]]
    value_input_option: str = "USER_ENTERED"

    @model_validator(mode="before")
    @classmethod
    def coerce_flat_values(cls, data: Any) -> Any:
        if isinstance(data, dict):
            v = data.get("values")
            if isinstance(v, list) and v and not isinstance(v[0], list):
                return {**data, "values": [v]}
        return data



@router.post("", status_code=201, response_model=SpreadsheetInfo)
async def create_spreadsheet(body: CreateSpreadsheetRequest):
    """Create a new Google Spreadsheet, optionally placed in a specific Drive folder."""
    token = await _get_access_token()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create the spreadsheet via the Sheets API
        resp = await client.post(
            SHEETS_API,
            json={"properties": {"title": body.title}},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            global _cached_token
            _cached_token = None
            token = await _get_access_token()
            resp = await client.post(
                SHEETS_API,
                json={"properties": {"title": body.title}},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code not in (200, 201):
            raise HTTPException(502, f"Sheets API error: {parse_google_error(resp.text)}")

        data = resp.json()
        spreadsheet_id = data["spreadsheetId"]

        # Move to the requested Drive folder if specified
        if body.folder_id:
            # Get current parents first
            meta_resp = await client.get(
                f"{DRIVE_API}/files/{spreadsheet_id}",
                params={"fields": "parents"},
                headers={"Authorization": f"Bearer {token}"},
            )
            current_parents = ",".join(meta_resp.json().get("parents", []))
            await client.patch(
                f"{DRIVE_API}/files/{spreadsheet_id}",
                params={"addParents": body.folder_id, "removeParents": current_parents},
                headers={"Authorization": f"Bearer {token}"},
            )

    sheets = [
        SheetTab(
            sheet_id=s["properties"]["sheetId"],
            title=s["properties"]["title"],
            row_count=s["properties"]["gridProperties"]["rowCount"],
            column_count=s["properties"]["gridProperties"]["columnCount"],
        )
        for s in data.get("sheets", [])
    ]
    return SpreadsheetInfo(id=spreadsheet_id, title=body.title, sheets=sheets)


@router.get("/{spreadsheet_id}", response_model=SpreadsheetInfo)
async def get_spreadsheet(spreadsheet_id: str = Path(..., description="The spreadsheet ID.")):
    """Get spreadsheet metadata: title and all sheet tab names, IDs, and dimensions."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _sheets_get(
            client,
            spreadsheet_id,
            params={"fields": "spreadsheetId,properties.title,sheets.properties"},
        )

    sheets = [
        SheetTab(
            sheet_id=s["properties"]["sheetId"],
            title=s["properties"]["title"],
            row_count=s["properties"]["gridProperties"]["rowCount"],
            column_count=s["properties"]["gridProperties"]["columnCount"],
        )
        for s in data.get("sheets", [])
    ]
    return SpreadsheetInfo(
        id=data["spreadsheetId"],
        title=data["properties"]["title"],
        sheets=sheets,
    )


@router.get("/{spreadsheet_id}/values/{range}")
async def read_sheet_values(
    spreadsheet_id: str = Path(..., description="The spreadsheet ID."),
    range: str = Path(..., description="A1 notation range, e.g. 'Sheet1!A1:D20' or 'Sheet1'."),
):
    """Read cell values from a spreadsheet range. Returns a 2D array of values."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _sheets_get(
            client,
            f"{spreadsheet_id}/values/{range}",
        )
    return {
        "range": data.get("range", range),
        "values": data.get("values", []),
        "row_count": len(data.get("values", [])),
    }


@router.put("/{spreadsheet_id}/values/{range}")
async def write_sheet_values(
    body: WriteValuesRequest,
    spreadsheet_id: str = Path(..., description="The spreadsheet ID."),
    range: str = Path(..., description="A1 notation range, e.g. 'Sheet1!A1:D5'."),
):
    """Overwrite values in a spreadsheet range. Existing values in the range are replaced."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _sheets_request(
            client,
            "PUT",
            f"{spreadsheet_id}/values/{range}",
            json={"range": range, "values": body.values, "majorDimension": "ROWS"},
            params={"valueInputOption": body.value_input_option},
        )
    return {
        "updated_range": data.get("updatedRange"),
        "updated_rows": data.get("updatedRows"),
        "updated_columns": data.get("updatedColumns"),
        "updated_cells": data.get("updatedCells"),
    }


@router.post("/{spreadsheet_id}/values/{range}/append")
async def append_sheet_rows(
    body: AppendRowsRequest,
    spreadsheet_id: str = Path(..., description="The spreadsheet ID."),
    range: str = Path(..., description="A1 notation range indicating the table to append to, e.g. 'Sheet1!A:D'."),
):
    """Append rows to a spreadsheet after the last row of existing data in the range."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _sheets_request(
            client,
            "POST",
            f"{spreadsheet_id}/values/{range}:append",
            json={"values": body.values, "majorDimension": "ROWS"},
            params={
                "valueInputOption": body.value_input_option,
                "insertDataOption": "INSERT_ROWS",
            },
        )
    updates = data.get("updates", {})
    return {
        "updated_range": updates.get("updatedRange"),
        "updated_rows": updates.get("updatedRows"),
        "updated_cells": updates.get("updatedCells"),
    }


@router.delete("/{spreadsheet_id}/values/{range}", status_code=200)
async def clear_sheet_range(
    spreadsheet_id: str = Path(..., description="The spreadsheet ID."),
    range: str = Path(..., description="A1 notation range to clear, e.g. 'Sheet1!A2:D10'."),
):
    """Clear all values in a range (formatting is preserved)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        data = await _sheets_request(
            client,
            "POST",
            f"{spreadsheet_id}/values/{range}:clear",
        )
    return {"cleared_range": data.get("clearedRange", range)}
