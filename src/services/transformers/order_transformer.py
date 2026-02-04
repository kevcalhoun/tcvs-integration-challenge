"""Order transformation service.

TODO: This is the main file candidates need to implement.

Transform Shopify orders to TecovaSuite sales orders.
"""

from src.models.shopify.order import ShopifyOrder
from src.models.tecovasuite.sales_order import TecovaSuiteSalesOrder
from src.utils.logging import get_logger

logger = get_logger(__name__)


class OrderTransformError(Exception):
    """Error during order transformation."""

    def __init__(self, message: str, shopify_order_id: int | None = None):
        super().__init__(message)
        self.shopify_order_id = shopify_order_id


def transform_shopify_order_to_tecovasuite(shopify_order: ShopifyOrder) -> TecovaSuiteSalesOrder:
    """Transform a Shopify order to a TecovaSuite sales order.

    TODO: Implement this function to convert Shopify order data to TecovaSuite format.

    This is the core transformation logic. You need to:

    1. CUSTOMER RESOLUTION
       - Map the Shopify customer to a TecovaSuite customer internal ID
       - Handle different customer scenarios (existing, new, guest checkout)

    2. ITEM ID MAPPING
       - Map Shopify product variants to TecovaSuite item IDs
       - Product mapping data is stored in Shopify metafields

    3. LINE ITEM TRANSFORMATION
       - Convert price formats between systems
       - Handle discounts and quantities correctly
       - Raise OrderTransformError if required data is missing

    4. ORDER FIELDS
       - Map order metadata (dates, IDs, notes) appropriately

    Args:
        shopify_order: The parsed Shopify order webhook payload

    Returns:
        A TecovaSuiteSalesOrder ready to be sent to the API

    Raises:
        OrderTransformError: If transformation fails due to missing or invalid data
    """
    logger.info(
        "Transforming Shopify order",
        shopify_order_id=shopify_order.id,
        order_number=shopify_order.order_number,
    )

    # TODO: Implement the transformation logic

    raise NotImplementedError(
        "TODO: Implement transform_shopify_order_to_tecovasuite()\n"
        "See the docstring above for the requirements."
    )
