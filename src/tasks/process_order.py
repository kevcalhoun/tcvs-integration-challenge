"""Celery tasks for processing Shopify order webhooks using chain pattern.

This implementation uses a three-step chained task pattern:
  1. fetch_order_data_task — fetch metafields and resolve customer
  2. transform_order_task — transform Shopify data to TecovaSuite format
  3. submit_order_task — create the order in TecovaSuite

Each task makes specific service calls, so failures are isolated and
retries only repeat the step that failed.
"""

from typing import Any

from celery import Task, chain

from src.celery_app import app
from src.models.shopify.order import ShopifyOrder
from src.services.customer_service import resolve_or_create_customer
from src.services.metafield_service import fetch_variant_metafields
from src.services.tecovasuite.client import TecovaSuiteClient, TecovaSuiteError
from src.services.transformers.order_transformer import (
    OrderTransformError,
    transform_shopify_order_to_tecovasuite,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


@app.task(
    bind=True,
    autoretry_for=(TecovaSuiteError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def fetch_order_data_task(
    self: Task, order_data: dict[str, Any]
) -> dict[str, Any]:
    """Fetch all required data for order processing.
    
    This task:
    1. Parses and validates the Shopify order
    2. Fetches product metafields (batch GraphQL query)
    3. Resolves or creates the customer
    
    Args:
        self: The Celery task instance (provides retry support)
        order_data: Raw order data from Shopify webhook
        
    Returns:
        Dict with:
            - shopify_order: Serialized ShopifyOrder
            - metafields: Dict of variant_id -> item_id mappings
            - customer_id: TecovaSuite customer internal_id
            
    Raises:
        ValidationError: If order data is invalid (non-retryable)
        TecovaSuiteError: If API calls fail (auto-retry)
    """
    shopify_order_id = order_data.get("id")
    
    logger.info(
        "Starting order data fetch",
        task_id=self.request.id,
        shopify_order_id=shopify_order_id,
        retry_count=self.request.retries,
    )
    
    # Phase 1: Parse and validate
    try:
        shopify_order = ShopifyOrder.model_validate(order_data)
    except Exception as e:
        logger.error(
            "Failed to parse Shopify order",
            shopify_order_id=shopify_order_id,
            error=str(e),
        )
        raise
    
    logger.info(
        "Parsed Shopify order",
        shopify_order_id=shopify_order.id,
        order_number=shopify_order.order_number,
        customer_email=shopify_order.customer.email if shopify_order.customer else None,
        item_count=len(shopify_order.line_items),
    )
    
    # Phase 2: Fetch metafields (batch GraphQL query)
    variant_ids = [
        item.variant_id for item in shopify_order.line_items if item.variant_id
    ]
    
    if not variant_ids:
        raise OrderTransformError(
            f"Order {shopify_order.id} has no variant IDs",
            shopify_order_id=shopify_order.id,
        )
    
    logger.info(
        "Fetching variant metafields",
        shopify_order_id=shopify_order.id,
        variant_count=len(variant_ids),
        variant_ids=variant_ids,
    )
    
    metafields = fetch_variant_metafields(variant_ids)
    
    logger.info(
        "Fetched metafields",
        shopify_order_id=shopify_order.id,
        found=len(metafields),
        missing=len(variant_ids) - len(metafields),
    )
    
    # Phase 3: Resolve customer
    email = shopify_order.customer.email if shopify_order.customer else None
    first_name = shopify_order.customer.first_name if shopify_order.customer else None
    last_name = shopify_order.customer.last_name if shopify_order.customer else None
    
    logger.info(
        "Resolving customer",
        shopify_order_id=shopify_order.id,
        email=email,
        first_name=first_name,
        last_name=last_name,
    )
    
    customer_id = resolve_or_create_customer(email, first_name, last_name)
    
    logger.info(
        "Customer resolved",
        shopify_order_id=shopify_order.id,
        customer_id=customer_id,
    )
    
    # Return enriched data for next task
    return {
        "shopify_order": shopify_order.model_dump(),
        "metafields": metafields,
        "customer_id": customer_id,
    }


@app.task(
    bind=True,
)
def transform_order_task(
    self: Task, enriched_data: dict[str, Any]
) -> dict[str, Any]:
    """Transform Shopify order data to TecovaSuite format.
    
    This is a pure transformation step with no external API calls.
    It should execute quickly and doesn't need retry logic.
    
    Args:
        self: The Celery task instance
        enriched_data: Data from fetch_order_data_task with:
            - shopify_order: Serialized ShopifyOrder
            - metafields: Dict of variant_id -> item_id mappings
            - customer_id: TecovaSuite customer internal_id
            
    Returns:
        Dict with:
            - tecovas_order: Serialized TecovaSuiteSalesOrder
            - shopify_order_id: Original Shopify order ID
            - shopify_order_number: Original Shopify order number
            
    Raises:
        OrderTransformError: If transformation fails (non-retryable)
    """
    shopify_order_dict = enriched_data["shopify_order"]
    metafields_raw = enriched_data["metafields"]
    metafields = {int(k): v for k, v in metafields_raw.items()}
    customer_id = enriched_data["customer_id"]
    
    shopify_order_id = shopify_order_dict["id"]
    
    logger.info(
        "Transforming order",
        task_id=self.request.id,
        shopify_order_id=shopify_order_id,
    )
    
    # Reconstruct Pydantic model
    shopify_order = ShopifyOrder.model_validate(shopify_order_dict)
    
    # Transform
    tecovas_order = transform_shopify_order_to_tecovasuite(
        shopify_order, customer_id, metafields
    )
    
    logger.info(
        "Order transformed",
        shopify_order_id=shopify_order.id,
        tecovas_customer_id=customer_id,
        item_count=len(tecovas_order.item),
    )
    
    return {
        "tecovas_order": tecovas_order.to_api_payload(),
        "shopify_order_id": shopify_order.id,
        "shopify_order_number": shopify_order.order_number,
        "customer_id": customer_id,
    }


@app.task(
    bind=True,
    autoretry_for=(TecovaSuiteError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def submit_order_task(
    self: Task, transformed_data: dict[str, Any]
) -> dict[str, Any]:
    """Submit the transformed order to TecovaSuite.
    
    Args:
        self: The Celery task instance (provides retry support)
        transformed_data: Data from transform_order_task with:
            - tecovas_order: Serialized TecovaSuiteSalesOrder
            - shopify_order_id: Original Shopify order ID
            - shopify_order_number: Original Shopify order number
            - customer_id: TecovaSuite customer internal_id
            
    Returns:
        Dict with final result including tecovas_order_id
        
    Raises:
        TecovaSuiteError: If API call fails (auto-retry)
    """
    tecovas_order_payload = transformed_data["tecovas_order"]
    shopify_order_id = transformed_data["shopify_order_id"]
    shopify_order_number = transformed_data["shopify_order_number"]
    customer_id = transformed_data["customer_id"]
    
    logger.info(
        "Submitting order to TecovaSuite",
        task_id=self.request.id,
        shopify_order_id=shopify_order_id,
        retry_count=self.request.retries,
    )
    
    client = TecovaSuiteClient()
    
    response = client.create_sales_order(tecovas_order_payload)
    
    tecovas_order_id = response.get("internal_id")
    
    logger.info(
        "Order created successfully",
        shopify_order_id=shopify_order_id,
        tecovas_order_id=tecovas_order_id,
        customer_id=customer_id,
    )
    
    return {
        "status": "success",
        "shopify_order_id": shopify_order_id,
        "shopify_order_number": shopify_order_number,
        "tecovas_order_id": tecovas_order_id,
        "customer_id": customer_id,
        "item_count": len(tecovas_order_payload.get("item", [])),
    }


def build_order_processing_chain(order_data: dict[str, Any]) -> chain:
    """Build a Celery chain for the order processing workflow.
    
    This creates a three-step chain:
      1. fetch_order_data_task — fetch metafields and resolve customer
      2. transform_order_task — transform to TecovaSuite format
      3. submit_order_task — create order in TecovaSuite
      
    Args:
        order_data: Raw order data from Shopify webhook
        
    Returns:
        A Celery chain ready to be applied or delayed
    """
    return chain(
        fetch_order_data_task.s(order_data),
        transform_order_task.s(),
        submit_order_task.s(),
    )
