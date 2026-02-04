"""Tests for Shopify models - these should pass without modification."""

from typing import Any

import pytest

from src.models.shopify.order import (
    ShopifyOrder,
    ShopifyLineItem,
    ShopifyCustomer,
    ShopifyMoney,
)


class TestShopifyMoney:
    """Tests for ShopifyMoney model."""

    def test_to_cents_whole_dollars(self) -> None:
        """Test conversion of whole dollar amounts."""
        money = ShopifyMoney(amount="295.00")
        assert money.to_cents() == 29500

    def test_to_cents_with_cents(self) -> None:
        """Test conversion of amounts with cents."""
        money = ShopifyMoney(amount="19.99")
        assert money.to_cents() == 1999

    def test_to_cents_zero(self) -> None:
        """Test conversion of zero."""
        money = ShopifyMoney(amount="0.00")
        assert money.to_cents() == 0


class TestShopifyLineItem:
    """Tests for ShopifyLineItem model."""

    def test_price_cents(self) -> None:
        """Test line item price conversion."""
        item = ShopifyLineItem(
            id=1,
            title="Test Item",
            quantity=1,
            price="295.00",
        )
        assert item.price_cents() == 29500

    def test_discount_cents(self) -> None:
        """Test line item discount conversion."""
        item = ShopifyLineItem(
            id=1,
            title="Test Item",
            quantity=1,
            price="295.00",
            total_discount="59.00",
        )
        assert item.discount_cents() == 5900

    def test_net_price_cents(self) -> None:
        """Test net price after discount."""
        item = ShopifyLineItem(
            id=1,
            title="Test Item",
            quantity=1,
            price="295.00",
            total_discount="59.00",
        )
        assert item.net_price_cents() == 23600  # 295 - 59 = 236


class TestShopifyOrder:
    """Tests for ShopifyOrder model."""

    def test_parse_simple_order(self, simple_order: dict[str, Any]) -> None:
        """Test parsing a simple order fixture."""
        order = ShopifyOrder.model_validate(simple_order)

        assert order.id == 5551234567890
        assert order.order_number == 1001
        assert order.email == "john.smith@email.com"
        assert len(order.line_items) == 1
        assert order.line_items[0].sku == "BOOT-CART-BLK-10"

    def test_parse_order_with_discount(self, order_with_discount: dict[str, Any]) -> None:
        """Test parsing an order with discounts."""
        order = ShopifyOrder.model_validate(order_with_discount)

        assert order.id == 5552345678901
        assert order.order_number == 1002
        assert len(order.line_items) == 2
        assert len(order.discount_codes) == 1
        assert order.discount_codes[0].code == "SAVE20"
        assert order.total_discounts == "77.00"

    def test_parse_order_multiple_items(self, order_multiple_items: dict[str, Any]) -> None:
        """Test parsing an order with multiple items."""
        order = ShopifyOrder.model_validate(order_multiple_items)

        assert order.id == 5553456789012
        assert order.order_number == 1003
        assert len(order.line_items) == 3

        # Check quantities
        assert order.line_items[0].quantity == 2  # 2x Earl boots
        assert order.line_items[1].quantity == 1  # 1x Knox boots
        assert order.line_items[2].quantity == 2  # 2x Care kits

    def test_order_totals_in_cents(self, simple_order: dict[str, Any]) -> None:
        """Test order total conversion to cents."""
        order = ShopifyOrder.model_validate(simple_order)

        assert order.total_cents() == 29500
        assert order.subtotal_cents() == 29500
        assert order.tax_cents() == 0

    def test_customer_data(self, simple_order: dict[str, Any]) -> None:
        """Test customer data is parsed correctly."""
        order = ShopifyOrder.model_validate(simple_order)

        assert order.customer is not None
        assert order.customer.email == "john.smith@email.com"
        assert order.customer.first_name == "John"
        assert order.customer.last_name == "Smith"
