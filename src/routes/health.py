"""Health check endpoint."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from src.config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint.

    Returns basic service health information.
    """
    settings = get_settings()

    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "environment": settings.environment,
        "service": "tecovas-integration-challenge",
    }
