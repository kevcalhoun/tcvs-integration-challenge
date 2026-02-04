"""Shopify order webhook payload models.

These models represent the structure of Shopify's orders/create webhook payload.
See: https://shopify.dev/docs/api/admin-rest/2024-01/resources/webhook

All models are complete and ready to use - no modifications needed.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ShopifyMoney(BaseModel):
    """Represents a monetary value in Shopify."""

    amount: str
    currency_code: str = "USD"

    def to_cents(self) -> int:
        """Convert string amount to integer cents.

        Shopify sends prices as strings like "295.00".
        TecovaSuite expects prices in cents like 29500.
        """
        return int(Decimal(self.amount) * 100)


class ShopifyAddress(BaseModel):
    """Shopify address model."""

    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    province: str | None = None
    province_code: str | None = None
    country: str | None = None
    country_code: str | None = None
    zip: str | None = None
    phone: str | None = None
    name: str | None = None


class ShopifyCustomer(BaseModel):
    """Shopify customer model."""

    id: int
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    default_address: ShopifyAddress | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    orders_count: int = 0
    total_spent: str = "0.00"
    tags: str = ""
    note: str | None = None


class ShopifyDiscountAllocation(BaseModel):
    """Discount allocation for a line item."""

    amount: str
    discount_application_index: int
    amount_set: dict[str, Any] | None = None


class ShopifyLineItem(BaseModel):
    """Shopify line item model."""

    id: int
    variant_id: int | None = None
    product_id: int | None = None
    title: str
    variant_title: str | None = None
    sku: str | None = None
    vendor: str | None = None
    quantity: int
    price: str  # String like "295.00"
    total_discount: str = "0.00"
    discount_allocations: list[ShopifyDiscountAllocation] = Field(default_factory=list)
    gift_card: bool = False
    taxable: bool = True
    tax_lines: list[dict[str, Any]] = Field(default_factory=list)
    fulfillment_status: str | None = None
    requires_shipping: bool = True
    properties: list[dict[str, Any]] = Field(default_factory=list)
    name: str = ""
    grams: int = 0

    def price_cents(self) -> int:
        """Get price in cents."""
        return int(Decimal(self.price) * 100)

    def discount_cents(self) -> int:
        """Get total discount in cents."""
        return int(Decimal(self.total_discount) * 100)

    def net_price_cents(self) -> int:
        """Get net price after discount in cents."""
        return self.price_cents() - self.discount_cents()


class ShopifyDiscountCode(BaseModel):
    """Shopify discount code applied to order."""

    code: str
    amount: str  # Discount amount as string
    type: str  # "percentage", "fixed_amount", "shipping"


class ShopifyShippingLine(BaseModel):
    """Shopify shipping line model."""

    id: int
    title: str
    price: str
    code: str | None = None
    source: str | None = None
    carrier_identifier: str | None = None
    discounted_price: str = "0.00"
    tax_lines: list[dict[str, Any]] = Field(default_factory=list)

    def price_cents(self) -> int:
        """Get shipping price in cents."""
        return int(Decimal(self.price) * 100)


class ShopifyOrder(BaseModel):
    """Shopify order webhook payload.

    This model represents the full orders/create webhook payload from Shopify.
    Only the most commonly used fields are included - Shopify sends many more.

    See: https://shopify.dev/docs/api/admin-rest/2024-01/resources/order
    """

    id: int
    name: str  # Order name like "#1001"
    order_number: int
    email: str | None = None
    phone: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    processed_at: datetime | None = None
    closed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None

    # Financial
    currency: str = "USD"
    subtotal_price: str  # Before tax and shipping
    total_price: str  # Final total
    total_tax: str = "0.00"
    total_discounts: str = "0.00"
    total_shipping_price_set: dict[str, Any] | None = None
    taxes_included: bool = False

    # Status
    financial_status: str | None = None  # "paid", "pending", "refunded", etc.
    fulfillment_status: str | None = None  # null, "fulfilled", "partial"

    # Customer
    customer: ShopifyCustomer | None = None
    billing_address: ShopifyAddress | None = None
    shipping_address: ShopifyAddress | None = None

    # Line items
    line_items: list[ShopifyLineItem] = Field(default_factory=list)

    # Discounts
    discount_codes: list[ShopifyDiscountCode] = Field(default_factory=list)
    discount_applications: list[dict[str, Any]] = Field(default_factory=list)

    # Shipping
    shipping_lines: list[ShopifyShippingLine] = Field(default_factory=list)

    # Additional fields
    note: str | None = None
    note_attributes: list[dict[str, Any]] = Field(default_factory=list)
    tags: str = ""
    source_name: str = "web"

    # Location
    location_id: int | None = None

    # Test mode
    test: bool = False

    def subtotal_cents(self) -> int:
        """Get subtotal in cents."""
        return int(Decimal(self.subtotal_price) * 100)

    def total_cents(self) -> int:
        """Get total in cents."""
        return int(Decimal(self.total_price) * 100)

    def tax_cents(self) -> int:
        """Get tax in cents."""
        return int(Decimal(self.total_tax) * 100)

    def discount_cents(self) -> int:
        """Get total discounts in cents."""
        return int(Decimal(self.total_discounts) * 100)
