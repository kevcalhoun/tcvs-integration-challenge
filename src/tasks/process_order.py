"""Celery task for processing Shopify orders.

This task orchestrates the complete order processing workflow:
1. Parse and validate Shopify webhook
2. Fetch variant metafields (batch GraphQL)
3. Resolve customer (find or create)
4. Transform order (Shopify → TecovaSuite format)
5. Submit to TecovaSuite API
6. Handle errors with appropriate retry logic

ARCHITECTURE: Single Task with Phases
======================================
We use a single task (not a chain) because:
- Matches the fulfillment example in the codebase
- Celery handles retry with exponential backoff automatically
- Simpler to reason about than task chains
- Clear error boundaries

RETRY STRATEGY:
--------------
The task is configured to auto-retry for TecovaSuiteError (network/API failures)
with exponential backoff:
- First retry: ~2 seconds
- Second retry: ~4 seconds  
- Third retry: ~8 seconds
- Max: 5 minutes between retries
- Give up after 3 retries

This handles temporary issues (rate limits, network blips, API downtime) while
not retrying forever on permanent failures.

ERROR TIERS:
-----------
Tier 1 (Don't Retry): OrderTransformError, ValidationError
  - Bad data, missing mappings
  - Retrying won't help
  - Log and alert for investigation

Tier 2 (Auto-Retry): TecovaSuiteError, ShopifyClientError  
  - Network issues, timeouts, API errors
  - Might succeed on retry
  - Celery handles automatically

Tier 3 (Graceful): CustomerResolutionError with fallback
  - Can't find customer
  - Create new one instead
  - Continue processing
"""

from typing import Any

from celery import Task
from pydantic import ValidationError

from src.celery_app import app
from src.models.shopify.order import ShopifyOrder
from src.services.customer_service import resolve_or_create_customer, CustomerResolutionError
from src.services.metafield_service import fetch_variant_metafields, MetafieldFetchError
from src.services.shopify.client import ShopifyClientError
from src.services.tecovasuite.client import TecovaSuiteClient, TecovaSuiteError
from src.services.transformers.order_transformer import (
    transform_shopify_order_to_tecovasuite,
    OrderTransformError,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


@app.task(
    bind=True,
    autoretry_for=(TecovaSuiteError, ShopifyClientError),
    retry_backoff=True,
    retry_backoff_max=300,  # Max 5 minutes between retries
    max_retries=3,
)
def process_shopify_order_task(self: Task, order_data: dict[str, Any]) -> dict[str, Any]:
    """Process a Shopify order and create it in TecovaSuite.

    This task orchestrates the complete order processing workflow with proper
    error handling and retry logic.

    IDEMPOTENCY:
    ------------
    This task should be called with a unique task_id based on the Shopify order ID:

        process_shopify_order_task.apply_async(
            task_id=f"order-{shopify_order_id}",
            args=[order_data]
        )

    This prevents duplicate processing if the same webhook is received multiple times.

    Args:
        self: Celery task instance (provides retry support)
        order_data: Raw Shopify order data as a dict from webhook

    Returns:
        Dict with processing result:
        {
            "status": "success",
            "shopify_order_id": 123,
            "tecovas_order_id": "5001",
            "customer_id": "101"
        }

    Raises:
        OrderTransformError: Non-retryable data errors
        ValidationError: Non-retryable validation errors
        TecovaSuiteError: Retryable API errors (auto-retry)
        ShopifyClientError: Retryable API errors (auto-retry)
    """
    logger.info(
        "Starting order processing task",
        order_id=order_data.get("id"),
        task_id=self.request.id,
        retry_count=self.request.retries,
    )

    # ==========================================
    # PHASE 1: PARSE AND VALIDATE
    # ==========================================
    # This phase validates the incoming data structure.
    # Errors here are non-retryable (bad data won't get better).

    try:
        shopify_order = ShopifyOrder.model_validate(order_data)
        logger.info(
            "Parsed Shopify order",
            shopify_order_id=shopify_order.id,
            order_number=shopify_order.order_number,
            customer_email=shopify_order.customer.email if shopify_order.customer else None,
            item_count=len(shopify_order.line_items),
        )
    except ValidationError as e:
        logger.error(
            "Invalid Shopify order payload",
            error=str(e),
            order_data=order_data,
        )
        # Don't retry - bad data won't get better
        raise

    # ==========================================
    # PHASE 2: FETCH VARIANT METAFIELDS
    # ==========================================
    # Batch GraphQL query to get TecovaSuite item IDs for all variants.
    # This is O(1) API calls instead of O(N).
    # Errors here are retryable (network issues).

    try:
        variant_ids = [item.variant_id for item in shopify_order.line_items if item.variant_id]

        if not variant_ids:
            raise OrderTransformError(
                f"Order {shopify_order.id} has no variant IDs",
                shopify_order_id=shopify_order.id,
            )

        logger.info(
            "Fetching variant metafields",
            variant_count=len(variant_ids),
            variant_ids=variant_ids,
        )

        item_mappings = fetch_variant_metafields(variant_ids)

        logger.info(
            "Fetched metafields",
            found=len(item_mappings),
            missing=len(variant_ids) - len(item_mappings),
        )

        # Check if any variants are missing metafields
        missing_variants = set(variant_ids) - set(item_mappings.keys())
        if missing_variants:
            logger.warning(
                "Some variants missing metafields",
                missing_count=len(missing_variants),
                missing_variants=list(missing_variants),
            )
            # We'll catch this in the transformer with a clear error

    except MetafieldFetchError as e:
        logger.error(
            "Failed to fetch variant metafields",
            error=str(e),
            variant_ids=variant_ids,
        )
        # Don't retry metafield errors - data issue
        raise OrderTransformError(
            f"Failed to fetch variant metafields: {e}",
            shopify_order_id=shopify_order.id,
        ) from e

    except ShopifyClientError:
        # Network error - let Celery retry
        logger.warning("Shopify API error, will retry")
        raise

    # ==========================================
    # PHASE 3: RESOLVE CUSTOMER
    # ==========================================
    # Find existing customer or create new one.
    # This handles guest checkout and dedupe logic.
    # API errors are retryable.

    try:
        customer_email = shopify_order.customer.email if shopify_order.customer else None
        customer_first_name = shopify_order.customer.first_name if shopify_order.customer else None
        customer_last_name = shopify_order.customer.last_name if shopify_order.customer else None

        logger.info(
            "Resolving customer",
            email=customer_email,
            first_name=customer_first_name,
            last_name=customer_last_name,
        )

        customer_id = resolve_or_create_customer(
            email=customer_email,
            first_name=customer_first_name,
            last_name=customer_last_name,
        )

        logger.info("Customer resolved", customer_id=customer_id)

    except CustomerResolutionError as e:
        logger.error(
            "Failed to resolve customer",
            error=str(e),
            customer_data={
                "email": customer_email,
                "first_name": customer_first_name,
                "last_name": customer_last_name,
            },
        )
        # Don't retry - data issue
        raise OrderTransformError(
            f"Failed to resolve customer: {e}",
            shopify_order_id=shopify_order.id,
        ) from e

    except TecovaSuiteError:
        # API error - let Celery retry
        logger.warning("TecovaSuite API error during customer resolution, will retry")
        raise

    # ==========================================
    # PHASE 4: TRANSFORM ORDER
    # ==========================================
    # Convert Shopify format to TecovaSuite format.
    # This is a pure function with no side effects.
    # Errors here are non-retryable (data issues).

    try:
        logger.info("Transforming order")

        tecovas_order = transform_shopify_order_to_tecovasuite(
            shopify_order=shopify_order,
            customer_id=customer_id,
            item_mappings=item_mappings,
        )

        logger.info(
            "Order transformed",
            tecovas_customer_id=tecovas_order.entity,
            item_count=len(tecovas_order.item),
        )

    except OrderTransformError:
        # Data error - don't retry
        logger.error("Order transformation failed")
        raise

    # ==========================================
    # PHASE 5: SUBMIT TO TECOVASUITE
    # ==========================================
    # Create the sales order in TecovaSuite.
    # This is the final step - errors here are retryable.

    try:
        logger.info("Submitting order to TecovaSuite")

        tecovas_client = TecovaSuiteClient()

        # Convert to API payload
        payload = tecovas_order.to_api_payload()

        logger.debug("TecovaSuite payload", payload=payload)

        # POST /api/v1/record/salesorder
        import httpx

        response = httpx.post(
            f"{tecovas_client.base_url}/record/salesorder",
            headers=tecovas_client._get_headers(),
            json=payload,
            timeout=30.0,
        )

        result = tecovas_client._handle_response(response)

        tecovas_order_id = result.get("internal_id")

        if not tecovas_order_id:
            raise TecovaSuiteError(
                "Order created but no internal_id returned",
                response=result,
            )

        logger.info(
            "Order created successfully",
            shopify_order_id=shopify_order.id,
            tecovas_order_id=tecovas_order_id,
            customer_id=customer_id,
        )

        # Return success result
        return {
            "status": "success",
            "shopify_order_id": shopify_order.id,
            "shopify_order_number": shopify_order.order_number,
            "tecovas_order_id": tecovas_order_id,
            "customer_id": customer_id,
            "item_count": len(tecovas_order.item),
        }

    except TecovaSuiteError:
        # API error - let Celery retry
        logger.warning("TecovaSuite API error during order creation, will retry")
        raise

    except Exception as e:
        # Unexpected error - wrap and don't retry
        logger.error("Unexpected error during order submission", error=str(e), exc_info=True)
        raise OrderTransformError(
            f"Unexpected error submitting order: {e}",
            shopify_order_id=shopify_order.id,
        ) from e
