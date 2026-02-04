"""Shopify webhook HMAC signature verification.

This utility verifies that incoming webhooks are actually from Shopify
by validating the HMAC signature in the X-Shopify-Hmac-Sha256 header.

See: https://shopify.dev/docs/apps/webhooks/configuration/https#step-5-verify-the-webhook

This module is complete and ready to use - no modifications needed.

Usage as FastAPI dependency:
    ```python
    from src.utils.shopify_hmac import require_valid_signature

    @router.post("/webhooks/orders/create")
    async def handle_order_created(body: bytes = Depends(require_valid_signature)):
        # body is already verified - if we get here, signature was valid
        order_data = json.loads(body)
        ...
    ```
"""

import base64
import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from src.config import get_settings


def verify_shopify_webhook(payload: bytes, hmac_header: str | None) -> bool:
    """Verify the HMAC signature of a Shopify webhook.

    Shopify signs all webhook payloads with a shared secret. This function
    computes the expected signature and compares it to the one provided
    in the X-Shopify-Hmac-Sha256 header.

    Args:
        payload: The raw request body as bytes
        hmac_header: The value of the X-Shopify-Hmac-Sha256 header

    Returns:
        True if the signature is valid, False otherwise

    Example:
        ```python
        @app.post("/webhooks/orders/create")
        async def handle_order_webhook(request: Request):
            payload = await request.body()
            hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")

            if not verify_shopify_webhook(payload, hmac_header):
                raise HTTPException(status_code=401, detail="Invalid signature")

            # Process the webhook...
        ```
    """
    if hmac_header is None:
        return False

    settings = get_settings()
    secret = settings.shopify_webhook_secret

    if not secret:
        # If no secret is configured, skip verification in development
        # WARNING: Never do this in production!
        if settings.environment == "development":
            return True
        return False

    # Compute the expected signature
    computed_hmac = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_hmac, hmac_header)


def compute_hmac(payload: bytes, secret: str) -> str:
    """Compute HMAC signature for testing purposes.

    Args:
        payload: The payload to sign
        secret: The secret key

    Returns:
        The base64-encoded HMAC signature
    """
    return base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")


async def require_valid_signature(request: Request) -> bytes:
    """FastAPI dependency that verifies Shopify webhook signatures.

    Use this as a dependency in your webhook endpoints to automatically
    verify the HMAC signature. If valid, returns the raw request body.
    If invalid, raises a 401 Unauthorized error.

    Args:
        request: The FastAPI request object (injected automatically)

    Returns:
        The raw request body as bytes (already verified)

    Raises:
        HTTPException: 401 if signature is missing or invalid

    Example:
        ```python
        from fastapi import Depends
        from src.utils.shopify_hmac import require_valid_signature

        @router.post("/webhooks/orders/create")
        async def handle_order_created(body: bytes = Depends(require_valid_signature)):
            order_data = json.loads(body)
            # Process the verified webhook...
        ```
    """
    body = await request.body()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")

    if not verify_shopify_webhook(body, hmac_header):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )

    return body


# Type alias for clean dependency injection
# Usage: async def handler(body: VerifiedWebhookBody) -> dict:
VerifiedWebhookBody = Annotated[bytes, Depends(require_valid_signature)]


