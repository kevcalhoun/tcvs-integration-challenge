"""Celery tasks for processing Shopify fulfillment webhooks.

This is a reference implementation showing a two-step chained task pattern:
  1. resolve_order_task — look up the TecovaSuite order by its external ID
  2. fulfill_order_task — mark the resolved order as fulfilled

This demonstrates how to build resilient, multi-step workflows with Celery
chains. Each task makes a single service call, so failures are isolated and
retries only repeat the step that failed.
"""

from typing import Any

from celery import Task, chain

from src.celery_app import app
from src.services.tecovasuite.client import TecovaSuiteClient, TecovaSuiteError
from src.utils.logging import get_logger

logger = get_logger(__name__)


@app.task(
    bind=True,
    autoretry_for=(TecovaSuiteError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def resolve_order_task(self: Task, fulfillment_data: dict[str, Any]) -> dict[str, Any]:
    """Resolve a Shopify order ID to a TecovaSuite internal_id.

    Looks up the sales order in TecovaSuite using the Shopify order_id
    as the external_id. Passes the resolved internal_id forward to the
    next task in the chain.

    Args:
        self: The Celery task instance (provides retry support)
        fulfillment_data: Parsed fulfillment data with keys:
            - order_id: Shopify order ID (used as external_id)
            - tracking_number: Optional tracking number
            - tracking_company: Optional carrier name

    Returns:
        The enriched fulfillment data with 'internal_id' added
    """
    order_id = fulfillment_data.get("order_id")
    fulfillment_id = fulfillment_data.get("id")

    logger.info(
        "Resolving order in TecovaSuite",
        fulfillment_id=fulfillment_id,
        order_id=order_id,
    )

    client = TecovaSuiteClient()
    order = client.get_sales_order_by_external_id(str(order_id))

    internal_id = order["internal_id"]
    logger.info(
        "Resolved order",
        fulfillment_id=fulfillment_id,
        order_id=order_id,
        internal_id=internal_id,
    )

    return {**fulfillment_data, "internal_id": internal_id}


@app.task(
    bind=True,
    autoretry_for=(TecovaSuiteError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def fulfill_order_task(self: Task, fulfillment_data: dict[str, Any]) -> dict[str, Any]:
    """Mark a sales order as fulfilled in TecovaSuite.

    Uses the internal_id (resolved by resolve_order_task) to call the
    TecovaSuite fulfill endpoint with optional tracking information.

    Args:
        self: The Celery task instance (provides retry support)
        fulfillment_data: Enriched fulfillment data with keys:
            - internal_id: TecovaSuite internal_id (from resolve step)
            - tracking_number: Optional tracking number
            - tracking_company: Optional carrier name

    Returns:
        A dict with the fulfillment result
    """
    internal_id = fulfillment_data["internal_id"]
    fulfillment_id = fulfillment_data.get("id")
    order_id = fulfillment_data.get("order_id")

    logger.info(
        "Fulfilling order in TecovaSuite",
        fulfillment_id=fulfillment_id,
        internal_id=internal_id,
    )

    client = TecovaSuiteClient()
    result = client.fulfill_sales_order(
        internal_id=internal_id,
        tracking_number=fulfillment_data.get("tracking_number"),
        tracking_company=fulfillment_data.get("tracking_company"),
    )

    logger.info(
        "Fulfillment processed successfully",
        fulfillment_id=fulfillment_id,
        internal_id=internal_id,
    )

    return {
        "status": "fulfilled",
        "fulfillment_id": fulfillment_id,
        "order_id": order_id,
        "internal_id": internal_id,
        "tecovasuite_response": result,
    }


def build_fulfillment_chain(fulfillment_data: dict[str, Any]) -> chain:
    """Build a Celery chain for the fulfillment workflow.

    This creates a two-step chain:
      1. resolve_order_task — look up internal_id by external_id
      2. fulfill_order_task — mark the order as fulfilled

    Args:
        fulfillment_data: Parsed fulfillment data from the webhook

    Returns:
        A Celery chain ready to be applied or delayed
    """
    return chain(
        resolve_order_task.s(fulfillment_data),
        fulfill_order_task.s(),
    )
