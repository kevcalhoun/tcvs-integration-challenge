"""TecovaSuite Sales Order models.

TODO: Implement these Pydantic models to match the TecovaSuite API schema.

API Documentation: https://tecovasuite.tecovas.workers.dev/docs/records/salesorder
"""


from pydantic import BaseModel, Field


class TecovaSuiteSalesOrderLine(BaseModel):
    """
    A line item in a TecovaSuite sales order.
    """

    item: str
    quantity: int
    rate: int
    discount: int | None = None
    tax: int | None = None
    description: str | None = None



class TecovaSuiteSalesOrder(BaseModel):
    """
    TecovaSuite sales order model.
    """

    entity: str
    item: list[TecovaSuiteSalesOrderLine] = Field(default_factory=list)
    trandate: str | None = None
    memo: str | None = None
    external_id: str | None = None
