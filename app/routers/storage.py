"""Storage endpoint - Google Drive + Photos initially, extensible to homelab NAS/S3."""

from fastapi import APIRouter

router = APIRouter()



# Drive operations
@router.get("/files")
async def list_files():
    """List files in Drive."""
    # TODO: Implement file listing
    return {"status": "not implemented"}


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Get file metadata or content."""
    # TODO: Implement file retrieval
    return {"status": "not implemented"}


@router.post("/files")
async def upload_file():
    """Upload a file to Drive."""
    # TODO: Implement file upload
    return {"status": "not implemented"}


# Photos operations
@router.get("/photos")
async def list_photos():
    """List photos from Google Photos."""
    # TODO: Implement photo listing
    return {"status": "not implemented"}


@router.get("/photos/albums")
async def list_albums():
    """List photo albums."""
    # TODO: Implement album listing
    return {"status": "not implemented"}
