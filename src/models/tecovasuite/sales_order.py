"""TecovaSuite Sales Order models.

These models define the data structure for creating sales orders in TecovaSuite.
They include validation to ensure data integrity before API submission.

API Documentation: https://tecovasuite.tecovasuite.workers.dev/docs/records/salesorder
"""

from typing import Any
from pydantic import BaseModel, Field, field_validator


class TecovaSuiteSalesOrderLine(BaseModel):
    """A line item in a TecovaSuite sales order.

    Represents a single product in an order with quantity, pricing, and optional
    discounts. Prices are in cents (e.g., $295.00 = 29500 cents).

    Attributes:
        item: TecovaSuite item internal ID (e.g., "201")
        quantity: Number of items (must be positive)
        rate: Unit price in cents (must be positive)
        discount: Optional discount amount in cents
        tax: Optional tax amount in cents
        description: Optional human-readable description
    """

    item: str = Field(..., description="TecovaSuite item internal ID")
    quantity: int = Field(..., gt=0, description="Quantity must be positive")
    rate: int = Field(..., gt=0, description="Rate in cents, must be positive")
    discount: int | None = Field(None, ge=0, description="Discount in cents")
    tax: int | None = Field(None, ge=0, description="Tax in cents")
    description: str | None = Field(None, description="Optional item description")

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """Ensure quantity is positive.

        Why: Can't order zero or negative items.
        Trade-off: Could allow zero for canceled items, but simpler to reject.
        """
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("rate")
    @classmethod
    def validate_rate(cls, v: int) -> int:
        """Ensure rate is positive.

        Why: Free items should have rate=0, but we reject that for data integrity.
        Trade-off: Could allow rate=0 for promos, but catches more errors this way.
        """
        if v <= 0:
            raise ValueError("Rate must be greater than zero")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for API payload, excluding None values.

        Why: TecovaSuite API doesn't want null fields in the payload.
        This keeps the payload clean and minimal.
        """
        data = {
            "item": self.item,
            "quantity": self.quantity,
            "rate": self.rate,
        }

        if self.discount is not None:
            data["discount"] = self.discount
        if self.tax is not None:
            data["tax"] = self.tax
        if self.description is not None:
            data["description"] = self.description

        return data


class TecovaSuiteSalesOrder(BaseModel):
    """TecovaSuite sales order model.

    Represents a complete order ready to be submitted to the TecovaSuite API.

    Design Decision: We require at least one item and a valid customer.
    This catches data errors early rather than failing at the API.

    Attributes:
        entity: TecovaSuite customer internal ID (e.g., "101")
        item: List of line items (at least one required)
        trandate: Transaction date in YYYY-MM-DD format
        memo: Optional order notes (useful for Shopify order #)
        external_id: External reference ID for idempotency (Shopify order ID)
    """

    entity: str = Field(..., description="TecovaSuite customer internal ID")
    item: list[TecovaSuiteSalesOrderLine] = Field(
        ..., min_length=1, description="Order line items (at least one required)"
    )
    trandate: str | None = Field(None, description="Transaction date (YYYY-MM-DD)")
    memo: str | None = Field(None, description="Order memo/notes")
    external_id: str | None = Field(None, description="External system reference ID")

    @field_validator("item")
    @classmethod
    def validate_items(cls, v: list[TecovaSuiteSalesOrderLine]) -> list[TecovaSuiteSalesOrderLine]:
        """Ensure at least one item exists.

        Why: An order without items is invalid and would fail at the API anyway.
        Better to catch it here with a clear error message.

        Trade-off: Could allow empty orders for "canceled" orders, but that's
        a different workflow. For order creation, we need items.
        """
        if not v:
            raise ValueError("Order must have at least one item")
        return v

    def to_api_payload(self) -> dict[str, Any]:
        """Convert to API payload format.

        Why: The API expects a specific JSON structure. This method handles
        the conversion and ensures optional fields are handled correctly.

        Design: We exclude fields that are None to keep the payload minimal.
        The API doesn't require these fields, so we don't send them.

        Returns:
            Dictionary ready to be JSON-encoded and sent to TecovaSuite API
        """
        payload: dict[str, Any] = {
            "entity": self.entity,
            "item": [item.to_dict() for item in self.item],
        }

        # Only include optional fields if they have values
        if self.trandate is not None:
            payload["trandate"] = self.trandate
        if self.memo is not None:
            payload["memo"] = self.memo
        if self.external_id is not None:
            payload["external_id"] = self.external_id

        return payload
