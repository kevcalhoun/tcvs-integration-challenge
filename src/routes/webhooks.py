"""
Shopify webhook handlers.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from src.models.shopify.fulfillment import ShopifyFulfillment
from src.tasks.process_fulfillment import build_fulfillment_chain
from src.utils.logging import get_logger
from src.utils.shopify_hmac import VerifiedWebhookBody

logger = get_logger(__name__)

router = APIRouter()


@router.post("/orders/create")
async def handle_order_created(body: Any) -> dict[str, Any]:
    """Handle Shopify orders/create webhook.

    TODO: Implement this webhook handler.

    Requirements:
    - Parse and validate the incoming order data
    - Queue for async processing
    - Respond quickly (Shopify expects response within 5 seconds)

    Args:
        body: The incoming webhook payload

    Returns:
        A dict with status and order_id

    Raises:
        HTTPException: 401 if signature invalid, 400 if payload invalid
    """
    logger.info("Received orders/create webhook %s", body)

    # TODO: Implement webhook handling

    raise HTTPException(
        status_code=501,
        detail="TODO: Implement the orders/create webhook handler",
    )


@router.post("/fulfillments/create")
async def handle_fulfillment_created(body: VerifiedWebhookBody) -> dict[str, Any]:
    """Handle Shopify fulfillments/create webhook.

    Dispatches a two-step Celery chain:
      1. Resolve the Shopify order ID to a TecovaSuite internal_id
      2. Mark the order as fulfilled with tracking info

    Args:
        body: The verified raw webhook payload bytes

    Returns:
        A dict with status and fulfillment_id

    Raises:
        HTTPException: 400 if the payload cannot be parsed
    """
    logger.info("Received fulfillments/create webhook")

    try:
        fulfillment = ShopifyFulfillment.model_validate_json(body)
    except Exception as e:
        logger.error("Invalid fulfillment payload", error=str(e))
        raise HTTPException(status_code=400, detail=f"Invalid fulfillment payload: {e}") from e

    logger.info(
        "Parsed fulfillment webhook",
        fulfillment_id=fulfillment.id,
        order_id=fulfillment.order_id,
        status=fulfillment.status,
    )

    build_fulfillment_chain(fulfillment.model_dump()).apply_async()

    return {"status": "queued", "fulfillment_id": fulfillment.id}
