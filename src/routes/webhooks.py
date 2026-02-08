"""Shopify webhook handler for orders/create.

This endpoint receives Shopify order webhooks and queues them for async processing.

ARCHITECTURE: Fast Response Pattern
====================================
Shopify expects webhook responses within 5 seconds. My solution achieves this by:
1. Validate HMAC signature (fast, cryptographic)
2. Parse basic structure (fast, minimal validation)
3. Queue for async processing (instant)
4. Return 200 immediately

The actual processing happens in the Celery worker, which can take longer.

SECURITY: HMAC Validation
==========================
Shopify signs all webhooks with HMAC-SHA256. We MUST verify this signature
to ensure the request actually came from Shopify and wasn't tampered with.

The verification uses the raw request body and the webhook secret from .env

IDEMPOTENCY: Task ID Strategy
==============================
We use the Shopify order ID as the Celery task ID. This provides automatic
idempotency because Celery won't run the same task_id twice concurrently.

If a webhook is replayed:
- Same task_id → Celery deduplicates automatically
- Different order_id → New task created (correct)

This is simpler than Redis locks or database checks.
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.models.shopify.order import ShopifyOrder
from src.tasks.process_order import process_shopify_order_task
from src.utils.logging import get_logger
from src.utils.shopify_hmac import verify_shopify_webhook

logger = get_logger(__name__)

router = APIRouter()


@router.post("/orders/create")
async def handle_order_created(request: Request) -> dict[str, Any]:
    """Handle Shopify orders/create webhook.

    WORKFLOW:
    1. Read raw body (needed for HMAC verification)
    2. Verify HMAC signature (security)
    3. Parse JSON payload
    4. Basic validation (has required fields)
    5. Queue Celery task with unique task_id (idempotency)
    6. Return 200 quickly (< 5 seconds)

    The actual order processing happens asynchronously in the Celery worker.

    Security:
        - HMAC verification prevents forged requests
        - Only accepts webhooks signed with our secret
        - Raw body is used for HMAC (before parsing)

    Idempotency:
        - Uses Shopify order ID as Celery task_id
        - Duplicate webhooks are automatically deduplicated
        - No extra infrastructure needed

    Args:
        request: FastAPI request object (provides raw body and headers)

    Returns:
        Dict with status and queued task information:
        {
            "status": "queued",
            "order_id": 123,
            "task_id": "order-123"
        }

    Raises:
        HTTPException:
            - 401: Invalid HMAC signature (security)
            - 400: Invalid payload structure (bad data)
            - 500: Internal error (logged for investigation)

    Example Shopify Webhook:
        POST /webhooks/orders/create
        Headers:
            X-Shopify-Hmac-SHA256: <signature>
            X-Shopify-Shop-Domain: store.myshopify.com
            X-Shopify-Topic: orders/create
        Body:
            {
                "id": 5551234567890,
                "name": "#1001",
                "customer": {...},
                "line_items": [...]
            }
    """
    logger.info(
        "Received orders/create webhook",
        shop_domain=request.headers.get("X-Shopify-Shop-Domain"),
        topic=request.headers.get("X-Shopify-Topic"),
    )

    # ==========================================
    # STEP 1: READ RAW BODY
    # ==========================================
    # We need the raw bytes for HMAC verification.
    # Must read before parsing JSON.

    try:
        raw_body = await request.body()
        logger.debug("Read request body", size_bytes=len(raw_body))
    except Exception as e:
        logger.error("Failed to read request body", error=str(e))
        raise HTTPException(
            status_code=400,
            detail="Could not read request body",
        ) from e

    # ==========================================
    # STEP 2: VERIFY HMAC SIGNATURE
    # ==========================================
    # ensures the request came from Shopify.
    # CRITICAL: Do this before trusting any data.

    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256")

    if not hmac_header:
        logger.warning("Missing HMAC header")
        raise HTTPException(
            status_code=401,
            detail="Missing X-Shopify-Hmac-SHA256 header",
        )

    try:
        is_valid = verify_shopify_webhook(raw_body, hmac_header)

        if not is_valid:
            logger.warning(
                "Invalid HMAC signature",
                shop_domain=request.headers.get("X-Shopify-Shop-Domain"),
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature",
            )

        logger.debug("HMAC signature verified")

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error("HMAC verification failed", error=str(e))
        raise HTTPException(
            status_code=401,
            detail=f"HMAC verification error: {e}",
        ) from e

    # ==========================================
    # STEP 3: PARSE JSON PAYLOAD
    # ==========================================
    # Convert raw bytes to dict.
    # We do minimal parsing here to keep response fast.

    try:
        order_data = json.loads(raw_body)
        logger.debug("Parsed JSON payload")
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON payload", error=str(e))
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON: {e}",
        ) from e

    # ==========================================
    # STEP 4: BASIC VALIDATION
    # ==========================================
    # Just check we have an order ID.
    # Full validation happens in the Celery task.

    order_id = order_data.get("id")

    if not order_id:
        logger.error("Missing order ID in payload", order_data=order_data)
        raise HTTPException(
            status_code=400,
            detail="Order payload missing 'id' field",
        )

    order_number = order_data.get("order_number", "unknown")

    logger.info(
        "Validated webhook payload",
        order_id=order_id,
        order_number=order_number,
    )

    # ==========================================
    # STEP 5: QUEUE CELERY TASK (IDEMPOTENCY)
    # ==========================================
    # Use order_id as task_id for automatic deduplication.
    # Celery won't run the same task_id twice concurrently.

    task_id = f"order-{order_id}"

    try:
        logger.info(
            "Queueing order processing task",
            order_id=order_id,
            task_id=task_id,
        )

        # Queue the task with unique ID
        # apply_async() is non-blocking - returns immediately
        task = process_shopify_order_task.apply_async(
            task_id=task_id,
            args=[order_data],
        )

        logger.info(
            "Task queued successfully",
            order_id=order_id,
            task_id=task.id,
        )

    except Exception as e:
        logger.error(
            "Failed to queue task",
            error=str(e),
            order_id=order_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue order processing: {e}",
        ) from e

    # ==========================================
    # STEP 6: RETURN SUCCESS QUICKLY
    # ==========================================
    # Shopify expects response within 5 seconds.
    # We've queued the task, so we're done here.

    return {
        "status": "queued",
        "order_id": order_id,
        "order_number": order_number,
        "task_id": task_id,
        "message": "Order queued for processing",
    }
