"""Tests for the order transformer.

These tests validate basic structure and model behavior.
You should add additional tests for edge cases and scenarios you discover.
"""

from typing import Any

import pytest

from src.models.shopify.order import ShopifyOrder
from src.models.tecovasuite.sales_order import TecovaSuiteSalesOrder, TecovaSuiteSalesOrderLine
from src.services.transformers.order_transformer import transform_shopify_order_to_tecovasuite


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
        with pytest.raises(ValueError):
            TecovaSuiteSalesOrderLine(item="201", quantity=0, rate=29500)

        with pytest.raises(ValueError):
            TecovaSuiteSalesOrderLine(item="201", quantity=-1, rate=29500)

    def test_rate_must_be_positive(self) -> None:
        """Test that rate validation rejects zero or negative values."""
        with pytest.raises(ValueError):
            TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=0)

        with pytest.raises(ValueError):
            TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=-100)


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
        with pytest.raises(ValueError):
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
        # Optional fields should either be excluded or None
        assert payload.get("trandate") is None or "trandate" not in payload
        assert payload.get("memo") is None or "memo" not in payload


class TestOrderTransformer:
    """Tests for the order transformer - basic smoke test."""

    def test_transform_simple_order(self, simple_order: dict[str, Any]) -> None:
        """Test transforming a simple order.

        This is a basic smoke test. You should add tests for additional scenarios
        like discounts, multiple items, guest checkout, etc.
        """
        shopify_order = ShopifyOrder.model_validate(simple_order)
        result = transform_shopify_order_to_tecovasuite(shopify_order)

        # Check basic structure
        assert isinstance(result, TecovaSuiteSalesOrder)
        assert result.entity  # Has a customer
        assert len(result.item) >= 1  # Has items
        assert result.external_id  # Preserves Shopify ID for idempotency
