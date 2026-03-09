"""Places router — Google Places API (New) for place search and details."""

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.errors import parse_google_error

logger = logging.getLogger(__name__)
router = APIRouter()

PLACES_API = "https://places.googleapis.com/v1"

_SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.types,"
    "places.rating,places.userRatingCount,places.currentOpeningHours,"
    "places.internationalPhoneNumber,places.websiteUri,places.editorialSummary,places.priceLevel"
)

_DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,types,rating,userRatingCount,"
    "currentOpeningHours,regularOpeningHours,internationalPhoneNumber,"
    "websiteUri,editorialSummary,priceLevel,reviews"
)


class PlaceSearchRequest(BaseModel):
    query: str
    latitude: float | None = None
    longitude: float | None = None
    radius_meters: float = 5000.0
    max_results: int = 5


def _api_headers(field_mask: str) -> dict[str, str]:
    return {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": field_mask,
        "Content-Type": "application/json",
    }


def _check_key() -> None:
    if not settings.google_maps_api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")


@router.post("/search")
async def search_places(req: PlaceSearchRequest):
    """Search for places using natural language, with optional location bias."""
    _check_key()

    body: dict = {"textQuery": req.query, "maxResultCount": min(req.max_results, 20)}
    if req.latitude is not None and req.longitude is not None:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": req.latitude, "longitude": req.longitude},
                "radius": req.radius_meters,
            }
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PLACES_API}/places:searchText",
            headers=_api_headers(_SEARCH_FIELD_MASK),
            json=body,
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Places API error: {parse_google_error(resp.text)}")

    data = resp.json()
    places = data.get("places", [])
    return {"places": places, "count": len(places)}


@router.get("/{place_id:path}")
async def get_place_details(place_id: str):
    """Get full details for a specific place by its Place ID."""
    _check_key()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{PLACES_API}/places/{place_id}",
            headers=_api_headers(_DETAILS_FIELD_MASK),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Places API error: {parse_google_error(resp.text)}")

    return resp.json()
