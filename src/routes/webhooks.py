"""Shopify webhook handlers."""

from typing import Any

from fastapi import APIRouter, HTTPException

from src.models.shopify.fulfillment import ShopifyFulfillment
from src.tasks.process_fulfillment import build_fulfillment_chain
from src.utils.logging import get_logger
from src.utils.shopify_hmac import VerifiedWebhookBody
from src.utils.webhook_utils import (
    build_idempotent_task_id,
    parse_order_payload,
    queue_order_processing,
    validate_order_data,
)

logger = get_logger(__name__)
router = APIRouter()


@router.post("/orders/create")
async def handle_order_created(body: VerifiedWebhookBody) -> dict[str, Any]:
    """Handle Shopify orders/create webhook.
    
    Uses VerifiedWebhookBody for automatic HMAC verification.
    Queues a three-step chain for async processing.
    
    Args:
        body: Verified webhook payload (HMAC already validated)
        
    Returns:
        Status response with order and task IDs
    """
    logger.info("Received orders/create webhook")
    
    try:
        order_data = parse_order_payload(body)
        validate_order_data(order_data)
    except ValueError as e:
        logger.error("Invalid order payload", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    
    order_id = order_data["id"]
    order_number = order_data.get("order_number")
    
    logger.info(
        "Processing order webhook",
        order_id=order_id,
        order_number=order_number,
    )
    
    try:
        task_id = build_idempotent_task_id(order_id)
        chain_id = queue_order_processing(order_data, task_id)
    except Exception as e:
        logger.error("Failed to queue order processing", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to queue order")
    
    return {
        "status": "queued",
        "order_id": order_id,
        "order_number": order_number,
        "task_id": task_id,
        "chain_id": chain_id,
    }


@router.post("/fulfillments/create")
async def handle_fulfillment_created(body: VerifiedWebhookBody) -> dict[str, Any]:
    """Handle Shopify fulfillments/create webhook.
    
    Args:
        body: Verified webhook payload (HMAC already validated)
        
    Returns:
        Status response with fulfillment ID
    """
    logger.info("Received fulfillments/create webhook")
    
    try:
        fulfillment = ShopifyFulfillment.model_validate_json(body)
    except Exception as e:
        logger.error("Invalid fulfillment payload", error=str(e))
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
    
    logger.info(
        "Queuing fulfillment",
        fulfillment_id=fulfillment.id,
        order_id=fulfillment.order_id,
    )
    
    build_fulfillment_chain(fulfillment.model_dump()).apply_async()
    
    return {"status": "queued", "fulfillment_id": fulfillment.id}