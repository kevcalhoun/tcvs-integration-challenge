"""Celery task for processing Shopify orders.

TODO: Candidates implement the order processing task.
"""

from typing import Any

from celery import Task

from src.celery_app import app
from src.services.tecovasuite.client import TecovaSuiteError
from src.utils.logging import get_logger

logger = get_logger(__name__)


@app.task(
    bind=True,
    autoretry_for=(TecovaSuiteError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def process_shopify_order_task(self: Task, order_data: dict[str, Any]) -> dict[str, Any]:
    """Process a Shopify order and create it in TecovaSuite.

    TODO: Implement this Celery task.

    This task orchestrates the order processing workflow:
    - Parse and validate the order data
    - Transform to TecovaSuite format
    - Submit to TecovaSuite API
    - Handle errors appropriately

    Args:
        self: The Celery task instance (provides retry support)
        order_data: The raw Shopify order data as a dict

    Returns:
        A dict with the processing result including status and relevant IDs

    Error Handling:
        The task is configured to auto-retry for TecovaSuiteError (transient API failures).
        Consider which errors should retry vs which indicate permanent data issues.
    """
    logger.info("Starting order processing task", order_id=order_data.get("id"))

    # TODO: Implement the order processing logic

    raise NotImplementedError(
        "TODO: Implement process_shopify_order_task()"
    )
