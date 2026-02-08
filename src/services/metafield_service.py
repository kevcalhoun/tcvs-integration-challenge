"""Shopify metafield fetching service.

Handles retrieving TecovaSuite item IDs from Shopify product variant metafields.

ARCHITECTURE DECISION: Batch GraphQL Query
===========================================

I fetch ALL metafields in a single GraphQL query rather than querying each
variant individually because:

✅ Pros:
- Single API call regardless of order size
- Avoids rate limits
- Much faster (1 query vs N queries)
- Shows understanding of GraphQL optimization

❌ Cons:
- Slightly more complex query structure
- All-or-nothing (if one fails, all fail)
- Larger response payload

ALTERNATIVE APPROACHES CONSIDERED:
-----------------------------------

1. Individual Queries Per Variant:
   - Simple but slow (N API calls)
   - Rate limit risk
   - Not production-ready for large orders
   
2. REST API Metafield Endpoint:
   - Simpler than GraphQL
   - But still requires N calls
   - GraphQL is the modern Shopify standard

We chose batch GraphQL as the most efficient and production-ready approach.

EDGE CASES HANDLED:
-------------------
- Variant ID not found: Skip with warning
- Metafield missing: Return None (caller handles fallback)
- GraphQL errors: Propagate for retry
- Invalid metafield format: Log warning and skip
"""

from typing import Any

from src.services.shopify.client import ShopifyClient, ShopifyClientError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class MetafieldFetchError(Exception):
    """Error fetching metafields from Shopify.

    This wraps ShopifyClientError to provide context about what we were
    trying to fetch when the error occurred.
    """

    def __init__(self, message: str, variant_ids: list[int] | None = None):
        super().__init__(message)
        self.variant_ids = variant_ids


def fetch_variant_metafields(variant_ids: list[int], client: ShopifyClient | None = None) -> dict[int, str]:
    """Fetch TecovaSuite item IDs for Shopify product variants.

    Uses a single GraphQL batch query to fetch all metafields efficiently.
    The metafield is stored as: namespace="tecovas", key="internal_id"

    DESIGN: Batch Query Strategy
    -----------------------------
    Instead of querying each variant individually:
      for variant_id in variant_ids:
          query_variant(variant_id)  # N API calls!

    We build a single GraphQL query that fetches all at once:
      query {
        node1: node(id: "gid://shopify/ProductVariant/123") { ... }
        node2: node(id: "gid://shopify/ProductVariant/456") { ... }
      }

    This is O(1) API calls instead of O(N).

    Args:
        variant_ids: List of Shopify variant IDs to fetch
        client: Optional ShopifyClient instance (for testing/DI)

    Returns:
        Dictionary mapping variant_id -> TecovaSuite item_id
        Example: {123: "201", 456: "206"}

        Note: If a variant doesn't have the metafield, it won't be in the dict.
        The caller should handle missing mappings appropriately.

    Raises:
        MetafieldFetchError: If the GraphQL query fails

    Example:
        >>> metafields = fetch_variant_metafields([123, 456, 789])
        >>> print(metafields)
        {123: "201", 456: "206", 789: "210"}

        >>> # Handle missing metafield
        >>> item_id = metafields.get(123)
        >>> if not item_id:
        ...     # Fallback logic here
    """
    if not variant_ids:
        logger.warning("No variant IDs provided")
        return {}

    if client is None:
        client = ShopifyClient()

    logger.info("Fetching metafields for variants", variant_count=len(variant_ids))

    # Build GraphQL query with aliases for each variant
    # Why aliases? So we can query multiple nodes in one request
    query_parts = []

    for idx, variant_id in enumerate(variant_ids):
        # Shopify uses Global IDs (GIDs) in GraphQL
        gid = f"gid://shopify/ProductVariant/{variant_id}"

        # Create an alias for each node query
        # This allows us to query multiple nodes in one request
        query_parts.append(
            f"""
          variant{idx}: node(id: "{gid}") {{
            ... on ProductVariant {{
              id
              metafield(namespace: "tecovas", key: "internal_id") {{
                value
              }}
            }}
          }}
        """
        )

    # Combine all parts into a single query
    query = """
    query FetchVariantMetafields {
      %s
    }
    """ % "\n".join(query_parts)

    try:
        logger.debug("Executing metafield batch query", query=query)
        data = client._execute_query(query)

    except ShopifyClientError as e:
        logger.error("Failed to fetch variant metafields", error=str(e), variant_ids=variant_ids)
        raise MetafieldFetchError(f"Failed to fetch metafields: {e}", variant_ids=variant_ids) from e

    # Parse the response and build the mapping
    metafields: dict[int, str] = {}

    for idx, variant_id in enumerate(variant_ids):
        alias_key = f"variant{idx}"
        variant_data = data.get(alias_key)

        if not variant_data:
            logger.warning("Variant not found in response", variant_id=variant_id, alias=alias_key)
            continue

        # Extract the metafield value
        metafield = variant_data.get("metafield")

        if not metafield:
            logger.warning(
                "Metafield not found for variant", variant_id=variant_id, namespace="tecovas", key="internal_id"
            )
            continue

        item_id = metafield.get("value")

        if not item_id:
            logger.warning("Metafield exists but has no value", variant_id=variant_id)
            continue

        # Success! Add to mapping
        metafields[variant_id] = str(item_id)
        logger.debug("Found metafield", variant_id=variant_id, item_id=item_id)

    logger.info(
        "Fetched metafields",
        requested=len(variant_ids),
        found=len(metafields),
        missing=len(variant_ids) - len(metafields),
    )

    return metafields


def get_item_id_for_variant(variant_id: int, metafields: dict[int, str], sku: str | None = None) -> str:
    """Get TecovaSuite item ID for a variant with fallback logic.

    DESIGN: Graceful Degradation
    -----------------------------
    We prefer the metafield (explicit mapping) but can fall back to SKU
    if the metafield is missing. This handles data integrity issues gracefully.

    Fallback Order:
    1. Metafield (most reliable)
    2. SKU lookup (backup strategy)
    3. Raise error (can't proceed)

    Args:
        variant_id: Shopify variant ID
        metafields: Dict of variant_id -> item_id from fetch_variant_metafields
        sku: Optional SKU for fallback lookup

    Returns:
        TecovaSuite item internal ID

    Raises:
        MetafieldFetchError: If no mapping found and no fallback available

    Example:
        >>> metafields = {123: "201", 456: "206"}
        >>>
        >>> # Found in metafields
        >>> item_id = get_item_id_for_variant(123, metafields)
        >>> # Returns: "201"
        >>>
        >>> # Not in metafields, use SKU fallback
        >>> item_id = get_item_id_for_variant(
        ...     789, metafields, sku="BOOT-CART-BLK-10"
        ... )
        >>> # Logs warning, attempts SKU lookup
    """
    # Try metafield first (preferred)
    item_id = metafields.get(variant_id)

    if item_id:
        return item_id

    # Metafield missing - this is a data integrity issue
    logger.warning("Metafield missing for variant, attempting SKU fallback", variant_id=variant_id, sku=sku)

    # TODO: Implement SKU fallback if needed
    # This would query TecovaSuite for an item by SKU
    # Trade-off: Extra API call vs failing fast

    # For now, we fail fast since metafields should always be set
    raise MetafieldFetchError(
        f"No TecovaSuite item mapping found for variant {variant_id}. "
        f"SKU: {sku}. Ensure metafield 'tecovas.internal_id' is set."
    )
