"""Tests for order transformation.

Tests the refactored transformer with small, focused functions.
"""

import pytest
from typing import Any

from src.models.shopify.order import ShopifyOrder
from src.models.tecovasuite.sales_order import (
    TecovaSuiteSalesOrder,
    TecovaSuiteSalesOrderLine,
)
from src.services.transformers.order_transformer import (
    transform_shopify_order_to_tecovasuite,
    _format_order_date,
    _build_order_memo,
    _parse_price_to_cents,
    _calculate_line_discount,
    _calculate_line_tax,
)


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
        )
        result = line.to_dict()
        assert "discount" not in result
        assert "tax" not in result
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
        
        customer_id = "101"
        item_mappings = {12345: "201"}
        
        result = transform_shopify_order_to_tecovasuite(
            shopify_order, customer_id, item_mappings
        )
        
        # Rate should be original price (29500 cents)
        # Discount should be shown separately (4425 cents)
        assert result.item[0].rate == 29500
        assert result.item[0].discount == 4425

    def test_transform_order_with_tax(self, simple_order: dict[str, Any]) -> None:
        """Test transforming an order with tax."""
        simple_order["line_items"][0]["tax_lines"] = [
            {"price": "23.60", "rate": 0.08, "title": "State Tax"}
        ]
        
        shopify_order = ShopifyOrder.model_validate(simple_order)
        
        customer_id = "101"
        item_mappings = {12345: "201"}
        
        result = transform_shopify_order_to_tecovasuite(
            shopify_order, customer_id, item_mappings
        )
        
        # Rate: $295.00, Tax: $23.60 (shown separately)
        assert result.item[0].rate == 29500
        assert result.item[0].tax == 2360

    def test_transform_order_with_discount_and_tax(self, simple_order: dict[str, Any]) -> None:
        """Test transforming an order with both discount and tax."""
        simple_order["line_items"][0]["discount_allocations"] = [
            {"amount": "44.25", "discount_application_index": 0}
        ]
        simple_order["line_items"][0]["tax_lines"] = [
            {"price": "20.06", "rate": 0.08, "title": "State Tax"}
        ]
        
        shopify_order = ShopifyOrder.model_validate(simple_order)
        
        customer_id = "101"
        item_mappings = {12345: "201"}
        
        result = transform_shopify_order_to_tecovasuite(
            shopify_order, customer_id, item_mappings
        )
        
        # Rate: $295.00, Discount: $4s4.25, Tax: $20.06
        assert result.item[0].rate == 29500
        assert result.item[0].discount == 4425
        assert result.item[0].tax == 2006


class TestHelperFunctions:
    """Test individual helper functions."""

    def test_parse_price_to_cents(self) -> None:
        """Test price parsing."""
        assert _parse_price_to_cents("295.00") == 29500
        assert _parse_price_to_cents("90.00") == 9000
        assert _parse_price_to_cents("10.50") == 1050

    def test_calculate_line_discount(self) -> None:
        """Test discount calculation."""
        from src.models.shopify.order import ShopifyLineItem
        
        # No discount
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[],
        )
        assert _calculate_line_discount(line_item) == 0
        
        # Single discount
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            discount_allocations=[
                {"amount": "44.25", "discount_application_index": 0}
            ],
        )
        assert _calculate_line_discount(line_item) == 4425
        
        # Multiple discounts
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
        assert _calculate_line_discount(line_item) == 1500

    def test_calculate_line_tax(self) -> None:
        """Test tax calculation."""
        from src.models.shopify.order import ShopifyLineItem
        
        # No tax
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
        )
        assert _calculate_line_tax(line_item) == 0
        
        # With single tax
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="295.00",
            tax_lines=[{"price": "23.60", "rate": 0.08, "title": "State Tax"}],
        )
        assert _calculate_line_tax(line_item) == 2360
        
        # With multiple tax lines
        line_item = ShopifyLineItem(
            id=1,
            title="Test",
            quantity=1,
            price="100.00",
            tax_lines=[
                {"price": "5.00", "rate": 0.05, "title": "State Tax"},
                {"price": "2.00", "rate": 0.02, "title": "Local Tax"},
            ],
        )
        assert _calculate_line_tax(line_item) == 700  # $5 + $2 = $7

    def test_format_order_date(self) -> None:
        """Test date formatting."""
        assert _format_order_date("2026-02-07T12:34:56Z") == "2026-02-07"
        assert _format_order_date("2026-02-07T00:00:00Z") == "2026-02-07"

    def test_build_order_memo(self) -> None:
        """Test memo building."""
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