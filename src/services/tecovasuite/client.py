"""TecovaSuite API client.

This client handles HTTP communication with the TecovaSuite API.
The base implementation is provided - candidates may extend as needed.

API Documentation: https://tecovasuite.tecovas.workers.dev/docs
"""

from typing import Any

import httpx

from src.config import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class TecovaSuiteError(Exception):
    """Base exception for TecovaSuite API errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class TecovaSuiteAuthError(TecovaSuiteError):
    """Authentication error (401)."""
    pass


class TecovaSuiteValidationError(TecovaSuiteError):
    """Validation error (400)."""
    pass


class TecovaSuiteNotFoundError(TecovaSuiteError):
    """Resource not found (404)."""
    pass


class TecovaSuiteClient:
    """HTTP client for TecovaSuite API.

    This client provides basic methods for interacting with the TecovaSuite API.
    It handles authentication, request formatting, and error handling.

    API Documentation: https://tecovasuite.tecovas.workers.dev/docs
    """

    def __init__(self) -> None:
        """Initialize the TecovaSuite client."""
        settings = get_settings()
        self.base_url = settings.tecovasuite_api_url
        self.api_key = settings.tecovasuite_api_key

        if not self.api_key:
            logger.warning("TecovaSuite API key not configured")

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Handle API response and raise appropriate errors.

        Args:
            response: The httpx response object

        Returns:
            The parsed JSON response

        Raises:
            TecovaSuiteAuthError: For 401 responses
            TecovaSuiteValidationError: For 400 responses
            TecovaSuiteNotFoundError: For 404 responses
            TecovaSuiteError: For other error responses
        """
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text}

        if response.status_code == 401:
            error_msg = data.get("error", {}).get("message", "Authentication failed")
            raise TecovaSuiteAuthError(error_msg, response.status_code, data)

        if response.status_code == 400:
            error_msg = data.get("error", {}).get("message", "Validation failed")
            raise TecovaSuiteValidationError(error_msg, response.status_code, data)

        if response.status_code == 404:
            error_msg = data.get("error", {}).get("message", "Not found")
            raise TecovaSuiteNotFoundError(error_msg, response.status_code, data)

        if response.status_code >= 400:
            error_msg = data.get("error", {}).get("message", f"API error: {response.status_code}")
            raise TecovaSuiteError(error_msg, response.status_code, data)

        result: dict[str, Any] = data
        return result

    def get_customers(self) -> list[dict[str, Any]]:
        """Get all customers from TecovaSuite.

        Returns:
            List of customer records
        """
        logger.info("Fetching customers from TecovaSuite")

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/customer",
                headers=self._get_headers(),
            )
            data = self._handle_response(response)
            records: list[dict[str, Any]] = data.get("records", [])
            return records

    def get_customer(self, internal_id: str) -> dict[str, Any]:
        """Get a single customer by internal ID.

        Args:
            internal_id: The customer's internal ID

        Returns:
            The customer record
        """
        logger.info("Fetching customer", internal_id=internal_id)

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/customer/{internal_id}",
                headers=self._get_headers(),
            )
            return self._handle_response(response)

    def get_items(self) -> list[dict[str, Any]]:
        """Get all items from TecovaSuite.

        Returns:
            List of item records
        """
        logger.info("Fetching items from TecovaSuite")

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/item",
                headers=self._get_headers(),
            )
            data = self._handle_response(response)
            records: list[dict[str, Any]] = data.get("records", [])
            return records

    def get_item(self, internal_id: str) -> dict[str, Any]:
        """Get a single item by internal ID.

        Args:
            internal_id: The item's internal ID

        Returns:
            The item record
        """
        logger.info("Fetching item", internal_id=internal_id)

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/item/{internal_id}",
                headers=self._get_headers(),
            )
            return self._handle_response(response)

    def get_item_by_sku(self, sku: str) -> dict[str, Any] | None:
        """Find an item by SKU (itemid).

        This is a convenience method that fetches all items and filters
        by SKU. Useful for looking up items when you have the SKU from
        an external system.

        Args:
            sku: The item SKU/itemid (e.g., "BOOT-CART-BLK-10")

        Returns:
            The item record if found, None otherwise
        """
        logger.info("Looking up item by SKU", sku=sku)
        items = self.get_items()

        for item in items:
            if item.get("itemid") == sku:
                return item

        return None

    def get_sales_orders(self) -> list[dict[str, Any]]:
        """Get all sales orders from TecovaSuite.

        Returns:
            List of sales order records
        """
        logger.info("Fetching sales orders from TecovaSuite")

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/salesorder",
                headers=self._get_headers(),
            )
            data = self._handle_response(response)
            records: list[dict[str, Any]] = data.get("records", [])
            return records

    def get_sales_order(self, internal_id: str) -> dict[str, Any]:
        """Get a single sales order by internal ID.

        Args:
            internal_id: The order's internal ID

        Returns:
            The sales order record with full details
        """
        logger.info("Fetching sales order", internal_id=internal_id)

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/salesorder/{internal_id}",
                headers=self._get_headers(),
            )
            return self._handle_response(response)

    def get_sales_order_by_external_id(self, external_id: str) -> dict[str, Any]:
        """Look up a sales order by its external ID.

        Resolves an external reference (e.g., Shopify Order ID) to a
        TecovaSuite sales order record. This is typically the first step
        before performing operations like fulfillment.

        Args:
            external_id: The order's external ID (e.g., Shopify order ID)

        Returns:
            The sales order record

        Raises:
            TecovaSuiteNotFoundError: If no order matches the external_id
        """
        logger.info("Looking up sales order by external ID", external_id=external_id)

        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/record/salesorder",
                params={"external_id": external_id},
                headers=self._get_headers(),
            )
            return self._handle_response(response)
        
    def create_sales_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new sales order in TecovaSuite.
        
        Args:
            order_data: The sales order data to create
            
        Returns:
            The created sales order record with internal_id
            
        Raises:
            TecovaSuiteError: If the API returns an error
        """
        logger.info("Creating sales order in TecovaSuite")
        
        with httpx.Client() as client:
            response = client.post(
                f"{self.base_url}/record/salesorder",
                headers=self._get_headers(),
                json=order_data,
            )
            return self._handle_response(response)


    def fulfill_sales_order(
        self,
        internal_id: str,
        tracking_number: str | None = None,
        tracking_company: str | None = None,
    ) -> dict[str, Any]:
        """Mark a sales order as fulfilled in TecovaSuite.

        Sends fulfillment/shipping information for an order identified
        by its internal_id. Use get_sales_order_by_external_id() first
        to resolve an external reference to an internal_id.

        Args:
            internal_id: The order's TecovaSuite internal_id
            tracking_number: Optional shipping tracking number
            tracking_company: Optional shipping carrier name

        Returns:
            The updated sales order record
        """
        logger.info("Fulfilling sales order", internal_id=internal_id)

        payload: dict[str, Any] = {}
        if tracking_number is not None:
            payload["tracking_number"] = tracking_number
        if tracking_company is not None:
            payload["tracking_company"] = tracking_company

        with httpx.Client() as client:
            response = client.post(
                f"{self.base_url}/record/salesorder/{internal_id}/fulfill",
                headers=self._get_headers(),
                json=payload,
            )
            return self._handle_response(response)
