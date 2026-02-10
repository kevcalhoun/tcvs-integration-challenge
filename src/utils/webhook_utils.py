"""Webhook processing utilities.

Helper functions for webhook handling.
"""
import json
from typing import Any

from src.utils.logging import get_logger

logger = get_logger(__name__)


def parse_order_payload(raw_body: bytes) -> dict[str, Any]:
    """Parse webhook JSON payload.
    
    Args:
        raw_body: Raw bytes from webhook
        
    Returns:
        Parsed order data
        
    Raises:
        ValueError: If JSON is invalid
    """
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in webhook", error=str(e))
        raise ValueError(f"Invalid JSON payload: {e}")


def validate_order_data(order_data: dict[str, Any]) -> None:
    """Validate required order fields.
    
    Args:
        order_data: Parsed order data
        
    Raises:
        ValueError: If required fields are missing
    """
    if not order_data.get("id"):
        raise ValueError("Missing order ID in payload")


def build_idempotent_task_id(order_id: int) -> str:
    """Build idempotent task ID from Shopify order ID.
    
    Prevents duplicate processing of the same order.
    
    Args:
        order_id: Shopify order ID
        
    Returns:
        Task ID string (e.g., "order-7601339859230")
    """
    return f"order-{order_id}"


def queue_order_processing(order_data: dict[str, Any], task_id: str) -> str:
    """Queue order processing chain.
    
    Builds and applies the three-step processing chain.
    
    Args:
        order_data: Shopify order data
        task_id: Idempotent task identifier
        
    Returns:
        Chain ID for tracking
    """
    # Import here to avoid circular dependency
    from src.tasks.process_order import build_order_processing_chain
    
    logger.info(
        "Queuing order processing",
        order_id=order_data.get("id"),
        task_id=task_id,
    )
    
    chain = build_order_processing_chain(order_data)
    result = chain.apply_async(task_id=task_id)
    
    logger.info(
        "Chain queued successfully",
        task_id=task_id,
        chain_id=result.id,
    )
    
    return result.id