"""FastAPI application entry point."""

from typing import Any

from fastapi import FastAPI

from src.config import get_settings
from src.routes import health, webhooks

settings = get_settings()

app = FastAPI(
    title="Tecovas Integration Challenge",
    description="Shopify to TecovaSuite Integration Service",
    version="0.1.0",
)

# Register routes
app.include_router(health.router, tags=["Health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])


@app.get("/")
async def root() -> dict[str, Any]:
    """Root endpoint."""
    return {
        "service": "Tecovas Integration Challenge",
        "status": "online",
        "docs": "/docs",
    }
