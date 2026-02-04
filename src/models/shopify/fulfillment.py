"""Shopify fulfillment webhook payload model.

This model represents the structure of Shopify's fulfillments/create webhook payload.
Only the fields needed for the TecovaSuite integration are included.

See: https://shopify.dev/docs/api/admin-rest/2024-01/resources/fulfillment
"""

from pydantic import BaseModel


class ShopifyFulfillment(BaseModel):
    """Shopify fulfillment webhook payload.

    Sent by Shopify when an order is fulfilled (shipped). We use this to
    update the corresponding sales order in TecovaSuite with fulfillment
    status and tracking information.
    """

    id: int
    order_id: int
    status: str
    tracking_number: str | None = None
    tracking_company: str | None = None
    tracking_url: str | None = None
    created_at: str
