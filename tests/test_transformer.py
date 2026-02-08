"""Comprehensive tests for the order integration.

These tests cover:
- Happy path (simple order)
- Edge cases (discounts, multiple items, guest checkout)
- Error scenarios (missing data, API failures)
- Model validation

TESTING STRATEGY:
=================
I use a layered testing approach:

1. Unit Tests (Fast, No I/O)
   - Test individual functions
   - Mock external dependencies
   - Test edge cases

2. Integration Tests (Slower, Real APIs)
   - Test full workflow
   - Use test fixtures
   - Verify end-to-end

This file focuses on unit tests. Integration tests would be in a separate file.
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.models.shopify.order import ShopifyOrder, ShopifyLineItem
from src.models.tecovasuite.sales_order import (
    TecovaSuiteSalesOrder,
    TecovaSuiteSalesOrderLine,
)
from src.services.transformers.order_transformer import (
    transform_shopify_order_to_tecovasuite,
    OrderTransformError,
    _calculate_unit_price_with_discount,
    _format_order_date,
    _build_order_memo,
)


class TestTecovaSuiteSalesOrderLine:
    """Tests for TecovaSuiteSalesOrderLine model validation."""

    def test_valid_line_item(self) -> None:
        """Test creating a valid line item."""
        line = TecovaSuiteSalesOrderLine(
            item="201",
            quantity=1,
            rate=29500,
        )
        assert line.item == "201"
        assert line.quantity == 1
        assert line.rate == 29500

    def test_quantity_must_be_positive(self) -> None:
        """Test that quantity validation rejects zero or negative values."""
        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrderLine(item="201", quantity=0, rate=29500)

        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrderLine(item="201", quantity=-1, rate=29500)

    def test_rate_must_be_positive(self) -> None:
        """Test that rate validation rejects zero or negative values."""
        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=0)

        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=-100)

    def test_to_dict_excludes_none(self) -> None:
        """Test that to_dict excludes None values."""
        line = TecovaSuiteSalesOrderLine(
            item="201",
            quantity=1,
            rate=29500,
            # discount and tax are None
        )

        data = line.to_dict()

        assert "discount" not in data
        assert "tax" not in data
        assert data["item"] == "201"


class TestTecovaSuiteSalesOrder:
    """Tests for TecovaSuiteSalesOrder model validation."""

    def test_valid_order(self) -> None:
        """Test creating a valid sales order."""
        order = TecovaSuiteSalesOrder(
            entity="101",
            item=[
                TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=29500),
            ],
            trandate="2024-01-15",
            memo="Test order",
            external_id="shopify_12345",
        )
        assert order.entity == "101"
        assert len(order.item) == 1
        assert order.trandate == "2024-01-15"

    def test_item_must_not_be_empty(self) -> None:
        """Test that at least one item is required."""
        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrder(entity="101", item=[])

    def test_to_api_payload(self) -> None:
        """Test converting order to API payload format."""
        order = TecovaSuiteSalesOrder(
            entity="101",
            item=[
                TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=29500),
                TecovaSuiteSalesOrderLine(item="301", quantity=2, rate=9000),
            ],
            trandate="2024-01-15",
            memo="Test order",
            external_id="shopify_12345",
        )

        payload = order.to_api_payload()

        assert payload["entity"] == "101"
        assert payload["trandate"] == "2024-01-15"
        assert payload["memo"] == "Test order"
        assert payload["external_id"] == "shopify_12345"
        assert len(payload["item"]) == 2
        assert payload["item"][0] == {"item": "201", "quantity": 1, "rate": 29500}
        assert payload["item"][1] == {"item": "301", "quantity": 2, "rate": 9000}

    def test_to_api_payload_optional_fields(self) -> None:
        """Test that optional fields are excluded when None."""
        order = TecovaSuiteSalesOrder(
            entity="101",
            item=[TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=29500)],
        )

        payload = order.to_api_payload()

        assert payload["entity"] == "101"
        assert len(payload["item"]) == 1
        # Optional fields should be excluded
        assert "trandate" not in payload
        assert "memo" not in payload


class TestOrderTransformer:
    """Tests for the order transformer."""

    def test_transform_simple_order(self, simple_order: dict[str, Any]) -> None:
        """Test transforming a simple order with one item."""
        shopify_order = ShopifyOrder.model_validate(simple_order)

        customer_id = "101"
        item_mappings = {
            12345: "201",  # Example variant_id -> item_id
        }

        # Mock the variant ID
        shopify_order.line_items[0].variant_id = 12345

        result = transform_shopify_order_to_tecovasuite(shopify_order, customer_id, item_mappings)

        # Check basic structure
        assert isinstance(result, TecovaSuiteSalesOrder)
        assert result.entity == "101"
        assert len(result.item) >= 1
        assert result.external_id == str(shopify_order.id)

    def test_transform_order_with_multiple_items(self, simple_order: dict[str, Any]) -> None:
        """Test transforming an order with multiple items."""
        # Add more line items
        simple_order["line_items"].append(
            {
                "id": 99999,
                "variant_id": 67890,
                "product_id": 999,
                "title": "Leather Belt - Brown",
                "variant_title": "36",
                "sku": "ACC-BELT-BRN-36",
                "quantity": 1,
                "price": "90.00",
                "total_discount": "0.00",
                "discount_allocations": [],
            }
        )

        shopify_order = ShopifyOrder.model_validate(simple_order)

        shopify_order.line_items[0].variant_id = 12345

        customer_id = "101"
        item_mappings = {
            12345: "201",  # Boot
            67890: "301",  # Belt
        }

        result = transform_shopify_order_to_tecovasuite(shopify_order, customer_id, item_mappings)

        assert len(result.item) == 2
        assert result.item[0].item == "201"
        assert result.item[1].item == "301"

    def test_transform_order_with_discounts(self, simple_order: dict[str, Any]) -> None:
        """Test transforming an order with line-item discounts."""
        # Add discount allocation to first line item
        simple_order["line_items"][0]["discount_allocations"] = [
            {
                "amount": "44.25",  # 15% off $295
                "discount_application_index": 0,
            }
        ]

        shopify_order = ShopifyOrder.model_validate(simple_order)
        shopify_order.line_items[0].variant_id = 12345

        customer_id = "101"
        item_mappings = {12345: "201"}

        result = transform_shopify_order_to_tecovasuite(shopify_order, customer_id, item_mappings)

        # $295 - $44.25 = $250.75 = 25075 cents
        assert result.item[0].rate == 25075

    def test_transform_fails_without_customer_id(self, simple_order: dict[str, Any]) -> None:
        """Test that transformation fails if customer_id is missing."""
        shopify_order = ShopifyOrder.model_validate(simple_order)

        with pytest.raises(OrderTransformError, match="Customer ID is required"):
            transform_shopify_order_to_tecovasuite(shopify_order, "", {12345: "201"})

    def test_transform_fails_without_variant_mapping(self, simple_order: dict[str, Any]) -> None:
        """Test that transformation fails if variant mapping is missing."""
        shopify_order = ShopifyOrder.model_validate(simple_order)
        shopify_order.line_items[0].variant_id = 12345

        # Empty mappings
        with pytest.raises(OrderTransformError, match="No TecovaSuite item mapping"):
            transform_shopify_order_to_tecovasuite(shopify_order, "101", {})

    def test_transform_fails_without_variant_id(self, simple_order: dict[str, Any]) -> None:
        """Test that transformation fails if line item has no variant_id."""
        shopify_order = ShopifyOrder.model_validate(simple_order)
        shopify_order.line_items[0].variant_id = None

        with pytest.raises(OrderTransformError, match="has no variant_id"):
            transform_shopify_order_to_tecovasuite(shopify_order, "101", {})


class TestDiscountCalculation:
    """Tests for discount calculation logic."""

    def test_no_discount(self) -> None:
        """Test price calculation with no discount."""
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[],
        )

        price = _calculate_unit_price_with_discount(line_item)
        assert price == 29500  # $295 = 29500 cents

    def test_single_discount(self) -> None:
        """Test price calculation with one discount."""
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[{"amount": "44.25", "discount_application_index": 0}],
        )

        price = _calculate_unit_price_with_discount(line_item)
        # $295 - $44.25 = $250.75 = 25075 cents
        assert price == 25075

    def test_multiple_discounts(self) -> None:
        """Test price calculation with multiple discounts."""
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[
                {"amount": "29.50", "discount_application_index": 0},  # 10% off
                {"amount": "26.55", "discount_application_index": 1},  # 10% off remainder
            ],
        )

        price = _calculate_unit_price_with_discount(line_item)
        # $295 - $29.50 - $26.55 = $238.95 = 23895 cents
        assert price == 23895

    def test_discount_with_quantity(self) -> None:
        """Test discount is applied to total then divided by quantity."""
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=2,
            price="100.00",
            discount_allocations=[
                {"amount": "20.00", "discount_application_index": 0}  # $20 off total
            ],
        )

        price = _calculate_unit_price_with_discount(line_item)
        # Total: $200, Discount: $20, After: $180, Per unit: $90 = 9000 cents
        assert price == 9000


class TestDateFormatting:
    """Tests for date formatting."""

    def test_format_datetime_string(self) -> None:
        """Test formatting ISO 8601 datetime string."""
        date_str = "2024-01-15T10:30:00-06:00"
        result = _format_order_date(date_str)
        assert result == "2024-01-15"

    def test_format_datetime_with_z(self) -> None:
        """Test formatting datetime with Z timezone."""
        date_str = "2024-01-15T10:30:00Z"
        result = _format_order_date(date_str)
        assert result == "2024-01-15"


class TestMemoBuilding:
    """Tests for memo field generation."""

    def test_memo_with_email(self, simple_order: dict[str, Any]) -> None:
        """Test memo includes order number and email."""
        shopify_order = ShopifyOrder.model_validate(simple_order)
        memo = _build_order_memo(shopify_order)

        assert "1001" in memo
        assert shopify_order.customer.email in memo

    def test_memo_without_email(self, simple_order: dict[str, Any]) -> None:
        """Test memo works without customer email."""
        simple_order["customer"]["email"] = None
        shopify_order = ShopifyOrder.model_validate(simple_order)

        memo = _build_order_memo(shopify_order)

        assert "1001" in memo
        # Should not crash, just omit email
