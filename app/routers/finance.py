"""Plaid finance integration."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/link/token")
async def create_link_token():
    """Create Plaid Link token."""
    # TODO: Implement Plaid Link token creation
    return {"status": "not implemented"}


@router.post("/link/exchange")
async def exchange_public_token():
    """Exchange public token for access token."""
    # TODO: Implement token exchange
    return {"status": "not implemented"}


@router.get("/accounts")
async def get_accounts():
    """List connected accounts."""
    # TODO: Implement account retrieval
    return {"status": "not implemented"}


@router.get("/transactions")
async def get_transactions():
    """Fetch transactions."""
    # TODO: Implement transaction retrieval
    return {"status": "not implemented"}
