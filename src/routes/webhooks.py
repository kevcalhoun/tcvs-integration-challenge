"""Webhook handler for Shopify orders/create using chain pattern."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.tasks.process_order import build_order_processing_chain
from src.utils.logging import get_logger
from src.utils.shopify_hmac import verify_shopify_webhook

logger = get_logger(__name__)
router = APIRouter()


@router.post("/orders/create")
async def handle_order_created(request: Request) -> dict[str, Any]:
    """Handle Shopify orders/create webhook using chain pattern.
    
    Workflow:
    1. Read raw body for HMAC verification
    2. Verify webhook authenticity
    3. Parse JSON payload
    4. Build and queue Celery chain with idempotent task ID
    5. Return 200 OK quickly (< 5 seconds as required by Shopify)
    
    The chain will process:
    - Task 1: Fetch metafields + resolve customer
    - Task 2: Transform order data
    - Task 3: Submit to TecovaSuite
    
    Returns:
        dict with status and order info
        
    Raises:
        HTTPException: 401 if HMAC invalid, 400 if payload invalid, 500 if queue fails
    """
    # Step 1: Read raw body (needed for HMAC verification)
    raw_body = await request.body()
    
    # Step 2: Verify HMAC signature (security)
    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256")
    
    if not hmac_header:
        logger.warning("Missing HMAC header")
        raise HTTPException(status_code=401, detail="Missing HMAC signature")
    
    is_valid = verify_shopify_webhook(raw_body, hmac_header)
    
    if not is_valid:
        logger.warning("Invalid HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")
    
    # Log webhook receipt
    shop_domain = request.headers.get("X-Shopify-Shop-Domain")
    topic = request.headers.get("X-Shopify-Topic")
    
    logger.info(
        "Received orders/create webhook",
        shop_domain=shop_domain,
        topic=topic,
    )
    
    # Step 3: Parse JSON payload
    try:
        order_data = json.loads(raw_body)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse webhook payload", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # Step 4: Basic validation
    order_id = order_data.get("id")
    order_number = order_data.get("order_number")
    
    if not order_id:
        logger.error("Missing order ID in payload")
        raise HTTPException(status_code=400, detail="Missing order ID")
    
    logger.info(
        "Validated webhook payload",
        order_id=order_id,
        order_number=order_number,
    )
    
    # Step 5: Build and queue Celery chain
    # Use order_id as task_id for idempotency
    task_id = f"order-{order_id}"
    
    logger.info(
        "Building order processing chain",
        order_id=order_id,
        task_id=task_id,
    )
    
    try:
        # Build the chain
        processing_chain = build_order_processing_chain(order_data)
        
        # Apply with idempotent task ID
        # Note: task_id applies to the first task in the chain
        result = processing_chain.apply_async(task_id=task_id)
        
        logger.info(
            "Chain queued successfully",
            order_id=order_id,
            task_id=task_id,
            chain_id=result.id,
        )
        
    except Exception as e:
        logger.error(
            "Failed to queue chain",
            order_id=order_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to queue order processing")
    
    # Step 6: Return 200 OK quickly
    return {
        "status": "queued",
        "order_id": order_id,
        "order_number": order_number,
        "task_id": task_id,
        "chain_id": result.id,
    }
