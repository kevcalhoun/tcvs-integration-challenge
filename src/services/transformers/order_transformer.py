"""Order transformation service.

This is the core business logic that converts Shopify orders to TecovaSuite format.

ARCHITECTURE: Pure Function Design
===================================
This transformer is a pure function (no side effects) which makes it:
- Easy to test (no mocks needed for the function itself)
- Easy to reason about (input → output)
- Easy to debug (no hidden state)

All external dependencies (API calls) are handled by the caller (the Celery task).
This separation of concerns is a key design principle.

DESIGN DECISIONS:
-----------------
1. Prices in cents: TecovaSuite expects integer cents (e.g., $295 = 29500)
2. Discounts at line level: Applied proportionally per Shopify's allocation
3. Date format: ISO 8601 date only (YYYY-MM-DD)
4. Memo field: Includes Shopify order number for traceability
5. External ID: Shopify order ID for idempotency
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
    """Error during order transformation.

    This is a non-retryable error that indicates bad data or missing mappings.
    Unlike TecovaSuiteError (which triggers retry), this means we can't proceed
    with the data we have.
    """

    def __init__(self, message: str, shopify_order_id: int | None = None):
        super().__init__(message)
        self.shopify_order_id = shopify_order_id


def transform_shopify_order_to_tecovasuite(
    shopify_order: ShopifyOrder,
    customer_id: str,
    item_mappings: dict[int, str],
) -> TecovaSuiteSalesOrder:
    """Transform a Shopify order to a TecovaSuite sales order.

    This is a pure function that takes validated inputs and produces a TecovaSuite order.
    All external dependencies (customer lookup, metafield fetching) are handled by the
    caller and passed in as arguments.

    WORKFLOW:
    1. Validate inputs (customer, items)
    2. Transform each line item (price, quantity, discounts)
    3. Build order metadata (date, memo, external_id)
    4. Return validated TecovaSuiteSalesOrder

    Args:
        shopify_order: Parsed Shopify order from webhook
        customer_id: TecovaSuite customer internal ID (already resolved)
        item_mappings: Dict of variant_id -> TecovaSuite item_id (already fetched)

    Returns:
        TecovaSuiteSalesOrder ready to be sent to the API

    Raises:
        OrderTransformError: If transformation fails due to missing or invalid data

    Example:
        >>> order = ShopifyOrder.model_validate(webhook_data)
        >>> customer_id = "101"
        >>> item_mappings = {12345: "201", 67890: "301"}
        >>>
        >>> result = transform_shopify_order_to_tecovasuite(
        ...     order, customer_id, item_mappings
        ... )
        >>> print(result.entity)  # "101"
        >>> print(len(result.item))  # 2
    """
    logger.info(
        "Transforming Shopify order",
        shopify_order_id=shopify_order.id,
        order_number=shopify_order.order_number,
        customer_id=customer_id,
        line_item_count=len(shopify_order.line_items),
    )

    # Validate inputs
    if not customer_id:
        raise OrderTransformError(
            "Customer ID is required for order transformation",
            shopify_order_id=shopify_order.id,
        )

    if not shopify_order.line_items:
        raise OrderTransformError(
            f"Order {shopify_order.id} has no line items",
            shopify_order_id=shopify_order.id,
        )

    # Transform each line item
    tecovas_items: list[TecovaSuiteSalesOrderLine] = []

    for line_item in shopify_order.line_items:
        logger.debug(
            "Processing line item",
            line_item_id=line_item.id,
            variant_id=line_item.variant_id,
            sku=line_item.sku,
            quantity=line_item.quantity,
        )

        # Get TecovaSuite item ID from mapping
        if not line_item.variant_id:
            raise OrderTransformError(
                f"Line item {line_item.id} has no variant_id. " f"SKU: {line_item.sku}, Title: {line_item.title}",
                shopify_order_id=shopify_order.id,
            )

        item_id = item_mappings.get(line_item.variant_id)
        if not item_id:
            raise OrderTransformError(
                f"No TecovaSuite item mapping found for variant {line_item.variant_id}. "
                f"SKU: {line_item.sku}. Ensure metafield 'tecovas.internal_id' is set.",
                shopify_order_id=shopify_order.id,
            )

        # Calculate the final unit price after discounts
        # DESIGN: Shopify provides exact discount allocation per line item
        # This handles complex scenarios like multiple discounts, percentage + fixed, etc.
        unit_price = _calculate_unit_price_with_discount(line_item)

        # Build the line item
        tecovas_item = TecovaSuiteSalesOrderLine(
            item=item_id,
            quantity=line_item.quantity,
            rate=unit_price,
            description=f"{line_item.title} - {line_item.variant_title or 'Standard'}",
        )

        tecovas_items.append(tecovas_item)

        logger.debug(
            "Transformed line item",
            item_id=item_id,
            quantity=line_item.quantity,
            rate=unit_price,
        )

    # Build order metadata
    trandate = _format_order_date(shopify_order.created_at)
    memo = _build_order_memo(shopify_order)
    external_id = str(shopify_order.id)

    # Create the TecovaSuite order
    tecovas_order = TecovaSuiteSalesOrder(
        entity=customer_id,
        item=tecovas_items,
        trandate=trandate,
        memo=memo,
        external_id=external_id,
    )

    logger.info(
        "Order transformation complete",
        shopify_order_id=shopify_order.id,
        tecovas_customer_id=customer_id,
        item_count=len(tecovas_items),
        order_date=trandate,
    )

    return tecovas_order


def _calculate_unit_price_with_discount(line_item) -> int:
    """Calculate the final unit price in cents after applying discounts.

    DESIGN: Line-Item Discount Allocation
    ======================================
    Shopify provides exact discount amounts per line item in discount_allocations.
    This is more accurate than trying to calculate proportional discounts ourselves.

    Example:
        Item: $295.00, Qty: 2, Total: $590.00
        Discount: 15% off = $88.50
        Per item: ($590.00 - $88.50) / 2 = $250.75

    The discount_allocations field gives us the exact $88.50, not an approximation.

    Args:
        line_item: ShopifyLineItem with price and discount_allocations

    Returns:
        Unit price in cents (integer)

    Example:
        >>> line_item.price = "295.00"
        >>> line_item.quantity = 2
        >>> line_item.discount_allocations = [
        ...     {"amount": "88.50", "discount_application_index": 0}
        ... ]
        >>> price = _calculate_unit_price_with_discount(line_item)
        >>> print(price)  # 25075 (cents)
    """
    # Base price per unit in cents
    base_price_cents = int(Decimal(line_item.price) * 100)

    # Calculate total discount for this line item
    total_discount_cents = 0

    for allocation in line_item.discount_allocations:
        discount_amount = Decimal(allocation.amount)
        total_discount_cents += int(discount_amount * 100)

    logger.debug(
        "Calculating price with discount",
        base_price=base_price_cents,
        quantity=line_item.quantity,
        total_discount=total_discount_cents,
    )

    # Calculate the line total after discount
    line_total = (base_price_cents * line_item.quantity) - total_discount_cents

    # Calculate per-unit price (rounded)
    # DESIGN: We round to nearest cent to avoid fractional cents
    # This might result in 1-cent discrepancies on orders with odd quantities
    # but it's more important to use integer cents for the API
    unit_price = round(line_total / line_item.quantity)

    # Validate result
    if unit_price <= 0:
        logger.warning(
            "Calculated unit price is zero or negative",
            line_item_id=line_item.id,
            base_price=base_price_cents,
            discount=total_discount_cents,
            quantity=line_item.quantity,
            calculated_price=unit_price,
        )
        # DESIGN DECISION: Allow $0 items? Or raise error?
        # For now, we allow it (could be a free gift)
        # But we log a warning for visibility
        if unit_price < 0:
            raise OrderTransformError(
                f"Calculated negative price for line item {line_item.id}. "
                f"Base: ${base_price_cents/100:.2f}, "
                f"Discount: ${total_discount_cents/100:.2f}, "
                f"Qty: {line_item.quantity}"
            )

    return unit_price


def _format_order_date(order_date: datetime | str) -> str:
    """Format order date as YYYY-MM-DD for TecovaSuite.

    DESIGN: Date-Only Format
    ========================
    TecovaSuite expects dates without time (YYYY-MM-DD).
    We extract just the date portion from Shopify's ISO 8601 timestamp.

    Args:
        order_date: Shopify order created_at timestamp

    Returns:
        Date string in YYYY-MM-DD format

    Example:
        >>> _format_order_date("2024-01-15T10:30:00-06:00")
        "2024-01-15"
    """
    if isinstance(order_date, str):
        # Parse ISO 8601 string
        order_date = datetime.fromisoformat(order_date.replace("Z", "+00:00"))

    # Format as date-only
    return order_date.strftime("%Y-%m-%d")


def _build_order_memo(shopify_order: ShopifyOrder) -> str:
    """Build a memo field for the order.

    DESIGN: Traceability
    ====================
    The memo includes the Shopify order number so we can easily trace
    orders back to Shopify for customer service purposes.

    We also include the customer email for quick reference.

    Args:
        shopify_order: The Shopify order

    Returns:
        Memo string for the order

    Example:
        >>> memo = _build_order_memo(order)
        >>> print(memo)
        "Shopify Order #1001 (john@example.com)"
    """
    parts = [f"Shopify Order #{shopify_order.order_number}"]

    # Add customer email if available
    if shopify_order.customer and shopify_order.customer.email:
        parts.append(f"({shopify_order.customer.email})")

    return " ".join(parts)
