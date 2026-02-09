"""Tests for order processing chain tasks.

These tests verify the chain-based implementation works correctly.
"""

import pytest
from decimal import Decimal
from typing import Any

from src.models.shopify.order import ShopifyOrder
from src.models.tecovasuite.sales_order import (
    TecovaSuiteSalesOrder,
    TecovaSuiteSalesOrderLine,
)
from src.services.transformers.order_transformer import (
    transform_shopify_order_to_tecovasuite,
    _calculate_unit_price_with_discount,
    _format_order_date,
    _build_order_memo,
)


# Keep all your existing tests - they still work!
# The transformer tests don't need to change because the transformer
# is still a pure function.

# Only the task orchestration changed, not the business logic.


class TestTecovaSuiteSalesOrderLine:
    """Test TecovaSuite sales order line model validation."""

    def test_valid_line_item(self) -> None:
        """Test creating a valid line item."""
        line = TecovaSuiteSalesOrderLine(
            item="201",
            quantity=2,
            rate=29500,  # $295.00 in cents
        )
        assert line.item == "201"
        assert line.quantity == 2
        assert line.rate == 29500

    def test_quantity_must_be_positive(self) -> None:
        """Test that quantity must be positive."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrderLine(item="201", quantity=0, rate=29500)

        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrderLine(item="201", quantity=-1, rate=29500)

    def test_rate_must_be_positive(self) -> None:
        """Test that rate must be positive."""
        from pydantic import ValidationError

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
            amount=None,
        )
        result = line.to_dict()
        assert "amount" not in result
        assert result["item"] == "201"


class TestTecovaSuiteSalesOrder:
    """Test TecovaSuite sales order model validation."""

    def test_valid_order(self) -> None:
        """Test creating a valid order."""
        order = TecovaSuiteSalesOrder(
            entity="101",
            item=[
                TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=29500)
            ],
        )
        assert order.entity == "101"
        assert len(order.item) == 1

    def test_item_must_not_be_empty(self) -> None:
        """Test that item list cannot be empty."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TecovaSuiteSalesOrder(entity="101", item=[])

    def test_to_api_payload(self) -> None:
        """Test to_api_payload method."""
        order = TecovaSuiteSalesOrder(
            entity="101",
            item=[
                TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=29500)
            ],
            trandate="2026-02-07",
            memo="Test order",
        )
        payload = order.to_api_payload()
        assert payload["entity"] == "101"
        assert len(payload["item"]) == 1
        assert payload["trandate"] == "2026-02-07"
        assert payload["memo"] == "Test order"

    def test_to_api_payload_optional_fields(self) -> None:
        """Test that optional fields are excluded when None."""
        order = TecovaSuiteSalesOrder(
            entity="101",
            item=[
                TecovaSuiteSalesOrderLine(item="201", quantity=1, rate=29500)
            ],
        )
        payload = order.to_api_payload()
        assert "trandate" not in payload
        assert "memo" not in payload


class TestOrderTransformer:
    """Test order transformation logic."""

    @pytest.fixture
    def simple_order(self) -> dict[str, Any]:
        """Return a simple test order."""
        return {
            "id": 5551234567890,
            "name": "#1001",
            "order_number": 1001,
            "email": "john.smith@email.com",
            "created_at": "2026-02-07T12:00:00Z",
            "subtotal_price": "295.00",
            "total_price": "295.00",
            "customer": {
                "id": 123456,
                "email": "john.smith@email.com",
                "first_name": "John",
                "last_name": "Smith",
            },
            "line_items": [
                {
                    "id": 11111,
                    "variant_id": 12345,
                    "product_id": 999,
                    "title": "The Cartwright - Black",
                    "variant_title": "10",
                    "sku": "BOOT-CART-BLK-10",
                    "quantity": 1,
                    "price": "295.00",
                    "total_discount": "0.00",
                    "discount_allocations": [],
                }
            ],
        }

    def test_transform_simple_order(self, simple_order: dict[str, Any]) -> None:
        """Test transforming a simple order."""
        shopify_order = ShopifyOrder.model_validate(simple_order)
        
        # Mock the variant_id to match our mapping
        shopify_order.line_items[0].variant_id = 12345
        
        customer_id = "101"
        item_mappings = {12345: "201"}
        
        result = transform_shopify_order_to_tecovasuite(
            shopify_order, customer_id, item_mappings
        )
        
        assert result.entity == "101"
        assert len(result.item) >= 1
        assert result.external_id == str(shopify_order.id)

    def test_transform_order_with_multiple_items(self, simple_order: dict[str, Any]) -> None:
        """Test transforming an order with multiple items."""
        simple_order["line_items"].append({
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
        })
        
        shopify_order = ShopifyOrder.model_validate(simple_order)
        shopify_order.line_items[0].variant_id = 12345
        
        customer_id = "101"
        item_mappings = {
            12345: "201",
            67890: "301",
        }
        
        result = transform_shopify_order_to_tecovasuite(
            shopify_order, customer_id, item_mappings
        )
        
        assert len(result.item) == 2
        assert result.item[0].item == "201"
        assert result.item[1].item == "301"

    def test_transform_order_with_discounts(self, simple_order: dict[str, Any]) -> None:
        """Test transforming an order with discounts."""
        simple_order["line_items"][0]["discount_allocations"] = [
            {"amount": "44.25", "discount_application_index": 0}
        ]
        
        shopify_order = ShopifyOrder.model_validate(simple_order)
        shopify_order.line_items[0].variant_id = 12345
        
        customer_id = "101"
        item_mappings = {12345: "201"}
        
        result = transform_shopify_order_to_tecovasuite(
            shopify_order, customer_id, item_mappings
        )
        
        # $295.00 - $44.25 = $250.75 = 25075 cents
        assert result.item[0].rate == 25075


class TestDiscountCalculation:
    """Test discount calculation logic."""

    def test_no_discount(self) -> None:
        """Test calculating price with no discount."""
        from src.models.shopify.order import ShopifyLineItem
        
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[],
        )
        
        price = _calculate_unit_price_with_discount(line_item)
        assert price == 29500  # $295.00

    def test_single_discount(self) -> None:
        """Test calculating price with single discount."""
        from src.models.shopify.order import ShopifyLineItem
        
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[
                {"amount": "44.25", "discount_application_index": 0}
            ],
        )
        
        price = _calculate_unit_price_with_discount(line_item)
        assert price == 25075  # $250.75

    def test_multiple_discounts(self) -> None:
        """Test calculating price with multiple discounts."""
        from src.models.shopify.order import ShopifyLineItem
        
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="100.00",
            discount_allocations=[
                {"amount": "10.00", "discount_application_index": 0},
                {"amount": "5.00", "discount_application_index": 1},
            ],
        )
        
        price = _calculate_unit_price_with_discount(line_item)
        assert price == 8500  # $85.00

    def test_discount_with_quantity(self) -> None:
        """Test calculating price with quantity > 1."""
        from src.models.shopify.order import ShopifyLineItem
        
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=2,
            price="100.00",
            discount_allocations=[
                {"amount": "20.00", "discount_application_index": 0}
            ],
        )
        
        price = _calculate_unit_price_with_discount(line_item)
        # Total: ($100 * 2) - $20 = $180
        # Per unit: $180 / 2 = $90
        assert price == 9000


class TestDateFormatting:
    """Test date formatting logic."""

    def test_format_datetime_string(self) -> None:
        """Test formatting ISO 8601 datetime string."""
        date_str = "2026-02-07T12:34:56Z"
        result = _format_order_date(date_str)
        assert result == "2026-02-07"

    def test_format_datetime_with_z(self) -> None:
        """Test formatting datetime with Z suffix."""
        date_str = "2026-02-07T00:00:00Z"
        result = _format_order_date(date_str)
        assert result == "2026-02-07"


class TestMemoBuilding:
    """Test memo field building logic."""

    def test_memo_with_email(self) -> None:
        """Test building memo with email."""
        from src.models.shopify.order import ShopifyOrder
        
        order_data = {
            "id": 123,
            "name": "#1001",
            "order_number": 1001,
            "email": "john@example.com",
            "created_at": "2026-02-07T12:00:00Z",
            "subtotal_price": "100.00",
            "total_price": "100.00",
            "customer": {
                "id": 1,
                "email": "john@example.com",
                "first_name": "John",
                "last_name": "Doe",
            },
            "line_items": [],
        }
        order = ShopifyOrder.model_validate(order_data)
        memo = _build_order_memo(order)
        assert "1001" in memo
        assert "john@example.com" in memo

    def test_memo_without_email(self) -> None:
        """Test building memo without email."""
        from src.models.shopify.order import ShopifyOrder
        
        order_data = {
            "id": 123,
            "name": "#1001",
            "order_number": 1001,
            "email": None,
            "created_at": "2026-02-07T12:00:00Z",
            "subtotal_price": "100.00",
            "total_price": "100.00",
            "customer": None,
            "line_items": [],
        }
        order = ShopifyOrder.model_validate(order_data)
        memo = _build_order_memo(order)
        assert "1001" in memo
