"""Order transformation service.

Converts Shopify orders to TecovaSuite format.
Pure functions with clear, single responsibilities.
"""

from datetime import datetime
from decimal import Decimal

from src.models.shopify.order import ShopifyOrder
from src.models.tecovasuite.sales_order import (
    TecovaSuiteSalesOrder,
    TecovaSuiteSalesOrderLine,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class OrderTransformError(Exception):
    """Error during order transformation."""

    def __init__(self, message: str, shopify_order_id: int | None = None):
        super().__init__(message)
        self.shopify_order_id = shopify_order_id


def transform_shopify_order_to_tecovasuite(
    shopify_order: ShopifyOrder,
    customer_id: str,
    item_mappings: dict[int, str],
) -> TecovaSuiteSalesOrder:
    """Transform a Shopify order to TecovaSuite format.
    
    Pure function - all dependencies passed as arguments.
    
    Args:
        shopify_order: Parsed Shopify order
        customer_id: TecovaSuite customer internal ID
        item_mappings: Dict of variant_id -> TecovaSuite item_id
        
    Returns:
        TecovaSuiteSalesOrder ready for API submission
        
    Raises:
        OrderTransformError: If required data is missing
    """
    logger.info(
        "Transforming order",
        shopify_order_id=shopify_order.id,
        order_number=shopify_order.order_number,
        customer_id=customer_id,
        line_item_count=len(shopify_order.line_items),
    )

    _validate_transformation_inputs(shopify_order, customer_id)
    
    tecovas_items = _transform_line_items(
        shopify_order.line_items,
        item_mappings,
        shopify_order.id,
    )
    
    tecovas_order = TecovaSuiteSalesOrder(
        entity=customer_id,
        item=tecovas_items,
        trandate=_format_order_date(shopify_order.created_at),
        memo=_build_order_memo(shopify_order),
        external_id=str(shopify_order.id),
    )

    logger.info(
        "Order transformed successfully",
        shopify_order_id=shopify_order.id,
        item_count=len(tecovas_items),
    )

    return tecovas_order


def _validate_transformation_inputs(
    shopify_order: ShopifyOrder,
    customer_id: str,
) -> None:
    """Validate inputs before transformation.
    
    Raises:
        OrderTransformError: If validation fails
    """
    if not customer_id:
        raise OrderTransformError(
            "Customer ID required",
            shopify_order_id=shopify_order.id,
        )

    if not shopify_order.line_items:
        raise OrderTransformError(
            f"Order {shopify_order.id} has no line items",
            shopify_order_id=shopify_order.id,
        )


def _transform_line_items(
    line_items: list,
    item_mappings: dict[int, str],
    order_id: int,
) -> list[TecovaSuiteSalesOrderLine]:
    """Transform all line items.
    
    Args:
        line_items: Shopify line items
        item_mappings: Variant ID to item ID mapping
        order_id: Shopify order ID (for error context)
        
    Returns:
        List of TecovaSuite line items
    """
    tecovas_items = []
    
    for line_item in line_items:
        tecovas_item = _transform_single_line_item(
            line_item,
            item_mappings,
            order_id,
        )
        tecovas_items.append(tecovas_item)
    
    return tecovas_items


def _transform_single_line_item(
    line_item,
    item_mappings: dict[int, str],
    order_id: int,
) -> TecovaSuiteSalesOrderLine:
    """Transform a single line item.
    
    Args:
        line_item: Shopify line item
        item_mappings: Variant ID to item ID mapping
        order_id: Shopify order ID (for error context)
        
    Returns:
        TecovaSuite line item
    """
    logger.debug(
        "Transforming line item",
        line_item_id=line_item.id,
        variant_id=line_item.variant_id,
        sku=line_item.sku,
    )
    
    item_id = _get_tecovas_item_id(
        line_item.variant_id,
        item_mappings,
        line_item.sku,
        order_id,
    )
    
    base_price_cents = _parse_price_to_cents(line_item.price)
    discount_cents = _calculate_line_discount(line_item)
    
    return TecovaSuiteSalesOrderLine(
        item=item_id,
        quantity=line_item.quantity,
        rate=base_price_cents,
        discount=discount_cents if discount_cents > 0 else None,
        description=_build_item_description(line_item),
    )


def _get_tecovas_item_id(
    variant_id: int | None,
    item_mappings: dict[int, str],
    sku: str | None,
    order_id: int,
) -> str:
    """Get TecovaSuite item ID from variant mapping.
    
    Args:
        variant_id: Shopify variant ID
        item_mappings: Variant to item mapping
        sku: Product SKU (for error messages)
        order_id: Order ID (for error context)
        
    Returns:
        TecovaSuite item ID
        
    Raises:
        OrderTransformError: If mapping not found
    """
    if not variant_id:
        raise OrderTransformError(
            f"Line item has no variant_id. SKU: {sku}",
            shopify_order_id=order_id,
        )

    item_id = item_mappings.get(variant_id)
    if not item_id:
        raise OrderTransformError(
            f"No item mapping for variant {variant_id}. "
            f"SKU: {sku}. Check metafield 'tecovas.internal_id'.",
            shopify_order_id=order_id,
        )

    return item_id


def _parse_price_to_cents(price_str: str) -> int:
    """Convert price string to integer cents.
    
    Args:
        price_str: Price as string (e.g., "295.00")
        
    Returns:
        Price in cents (e.g., 29500)
    """
    return int(Decimal(price_str) * 100)


def _calculate_line_discount(line_item) -> int:
    """Calculate total discount for a line item.
    
    Shopify provides exact per-line discount allocation.
    
    Args:
        line_item: Shopify line item with discount_allocations
        
    Returns:
        Total discount in cents
    """
    total_discount_cents = 0

    for allocation in line_item.discount_allocations:
        discount_amount = Decimal(allocation.amount)
        total_discount_cents += int(discount_amount * 100)

    return total_discount_cents


def _build_item_description(line_item) -> str:
    """Build item description from line item.
    
    Args:
        line_item: Shopify line item
        
    Returns:
        Formatted description
    """
    variant_title = line_item.variant_title or "Standard"
    return f"{line_item.title} - {variant_title}"


def _format_order_date(order_date: datetime | str) -> str:
    """Format order date as YYYY-MM-DD.
    
    TecovaSuite expects date-only format.
    
    Args:
        order_date: ISO 8601 timestamp or datetime
        
    Returns:
        Date string (YYYY-MM-DD)
    """
    if isinstance(order_date, str):
        order_date = datetime.fromisoformat(order_date.replace("Z", "+00:00"))

    return order_date.strftime("%Y-%m-%d")


def _build_order_memo(shopify_order: ShopifyOrder) -> str:
    """Build order memo for traceability.
    
    Args:
        shopify_order: Shopify order
        
    Returns:
        Memo string
    """
    parts = [f"Shopify Order #{shopify_order.order_number}"]

    if shopify_order.customer and shopify_order.customer.email:
        parts.append(f"({shopify_order.customer.email})")

    return " ".join(parts)