"""Shopify GraphQL API client.

This client provides the infrastructure for executing GraphQL queries
against the Shopify Admin API.

Shopify GraphQL Documentation:
https://shopify.dev/docs/api/admin-graphql

Example usage:
    client = ShopifyClient()
    data = client._execute_query(query, variables)
"""

from typing import Any

import httpx

from src.config import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)

# GraphQL API version
API_VERSION = "2025-01"


class ShopifyClientError(Exception):
    """Error from Shopify API."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ShopifyClient:
    """Client for Shopify Admin GraphQL API.

    This client provides the infrastructure for executing GraphQL queries.
    Candidates should write their own queries to fetch variant metafields.

    Configuration (via environment variables):
    - SHOPIFY_STORE_DOMAIN: Your store domain (e.g., "my-store.myshopify.com")
    - SHOPIFY_ACCESS_TOKEN: Admin API access token
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.store_domain = settings.shopify_store_domain
        self.access_token = settings.shopify_access_token
        self.graphql_url = f"https://{self.store_domain}/admin/api/{API_VERSION}/graphql.json"

    def _execute_query(self, query: str, variables: dict[str, Any] | None = None) -> dict[Any, Any]:
        """Execute a GraphQL query against Shopify Admin API.

        Args:
            query: The GraphQL query string
            variables: Optional variables for the query

        Returns:
            The data portion of the GraphQL response

        Raises:
            ShopifyClientError: If the API returns an error
        """
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                self.graphql_url,
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": self.access_token,
                },
                json={"query": query, "variables": variables or {}},
            )

            if response.status_code != 200:
                logger.error(
                    "Shopify API error",
                    status_code=response.status_code,
                    response=response.text,
                )
                raise ShopifyClientError(
                    f"Shopify API error: {response.status_code}",
                    status_code=response.status_code,
                )

            result = response.json()

            if "errors" in result:
                error_msg = result["errors"][0].get("message", "Unknown error")
                logger.error("Shopify GraphQL error", errors=result["errors"])
                raise ShopifyClientError(f"GraphQL error: {error_msg}")

            data: dict[Any, Any] = result.get("data", {})
            return data
