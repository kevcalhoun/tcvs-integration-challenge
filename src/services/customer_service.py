"""Customer resolution service.

Handles finding or creating customers in TecovaSuite based on Shopify customer data.

ARCHITECTURE DECISION: Find-First-Then-Create Pattern
======================================================

We chose to search for existing customers before creating new ones because:

✅ Pros:
- Prevents duplicate customer records
- Realistic production behavior
- Customers can place multiple orders

❌ Cons:
- Extra API call on first order for new customers
- Race condition if two orders process simultaneously (rare, acceptable)
- Email-based matching isn't perfect (what if email changes?)

ALTERNATIVE APPROACHES CONSIDERED:
-----------------------------------

1. Always Create New Customer:
   - Simpler but creates duplicates (not production-ready)
   
2. Cache-Based Lookup (Redis):
   - Faster but adds complexity and cache invalidation issues
   
3. Database-Backed Mapping:
   - Most robust but requires additional infrastructure

We chose Find-First as the best balance of simplicity and correctness.

EDGE CASES HANDLED:
-------------------
- Guest checkout (no email): Create with name only
- Missing customer data: Raise clear error
- Multiple customers with same email: Use first match (acceptable)
- API failures: Propagate as TecovaSuiteError for retry
"""

from typing import Any

from src.services.tecovasuite.client import TecovaSuiteClient, TecovaSuiteError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class CustomerResolutionError(Exception):
    """Error during customer resolution.

    This is a non-retryable error that indicates bad data or configuration.
    Unlike TecovaSuiteError (network/API), this means we can't proceed.
    """

    pass


def resolve_or_create_customer(
    email: str | None, 
    first_name: str | None, 
    last_name: str | None, 
    client: TecovaSuiteClient | None = None
) -> str:
    """Find an existing customer or create a new one in TecovaSuite.

    This function implements a "find-first-then-create" pattern to avoid
    duplicate customer records while handling various edge cases.

    WORKFLOW:
    1. If email provided: Search for existing customer by email
    2. If found: Return existing customer ID
    3. If not found OR no email: Create new customer
    4. Return customer internal ID

    Args:
        email: Customer email (may be None for guest checkout)
        first_name: Customer first name (may be None)
        last_name: Customer last name (may be None)
        client: Optional TecovaSuiteClient instance (for testing/DI)

    Returns:
        TecovaSuite customer internal ID (e.g., "101")

    Raises:
        CustomerResolutionError: If customer data is insufficient
        TecovaSuiteError: If API calls fail (triggers Celery retry)

    Examples:
        >>> # Existing customer
        >>> customer_id = resolve_or_create_customer(
        ...     "john@example.com", "John", "Smith"
        ... )
        >>> # Returns: "101" (existing customer)

        >>> # New customer
        >>> customer_id = resolve_or_create_customer(
        ...     "new@example.com", "Jane", "Doe"
        ... )
        >>> # Returns: "105" (newly created)

        >>> # Guest checkout (no email)
        >>> customer_id = resolve_or_create_customer(
        ...     None, "Guest", "Buyer"
        ... )
        >>> # Returns: "106" (guest customer)
    """
    if client is None:
        client = TecovaSuiteClient()

    # Validate we have minimal customer data
    if not email and not (first_name or last_name):
        logger.error("Cannot resolve customer: no email or name provided")
        raise CustomerResolutionError("Insufficient customer data: need either email or name")

    # Step 1: Try to find existing customer by email
    if email:
        logger.info("Looking up customer by email", email=email)

        try:
            customers = client.get_customers()

            # Search for matching email (case-insensitive)
            # Why case-insensitive? john@Example.com == john@example.com
            email_lower = email.lower()
            for customer in customers:
                if customer.get("email", "").lower() == email_lower:
                    customer_id = customer["internal_id"]
                    logger.info("Found existing customer", email=email, customer_id=customer_id)
                    return customer_id

            logger.info("No existing customer found, will create new", email=email)

        except TecovaSuiteError as e:
            # Network/API error - let it propagate for Celery retry
            logger.error("Failed to fetch customers", error=str(e))
            raise

    # Step 2: Create new customer
    logger.info("Creating new customer", email=email, first_name=first_name, last_name=last_name)

    # Build customer payload
    customer_data: dict[str, Any] = {}

    if email:
        customer_data["email"] = email
    if first_name:
        customer_data["firstname"] = first_name
    if last_name:
        customer_data["lastname"] = last_name

    # If we have neither first nor last name, use a default
    # This handles edge case of guest checkout with no name
    if not first_name and not last_name:
        customer_data["firstname"] = "Guest"
        logger.warning("Creating customer with default name 'Guest'", email=email)

    try:
        # POST /api/v1/record/customer
        # Note: We're using the existing client's HTTP methods
        # The actual API endpoint details would be in the client implementation
        import httpx

        response = httpx.post(
            f"{client.base_url}/record/customer", headers=client._get_headers(), json=customer_data, timeout=10.0
        )

        result = client._handle_response(response)
        customer_id = result.get("internal_id")

        if not customer_id:
            raise CustomerResolutionError("Customer created but no internal_id returned")

        logger.info("Created new customer", customer_id=customer_id, email=email)
        return customer_id

    except TecovaSuiteError as e:
        # API error - let it propagate for retry
        logger.error("Failed to create customer", error=str(e))
        raise
    except Exception as e:
        # Unexpected error - wrap as non-retryable
        logger.error("Unexpected error creating customer", error=str(e))
        raise CustomerResolutionError(f"Failed to create customer: {e}") from e
