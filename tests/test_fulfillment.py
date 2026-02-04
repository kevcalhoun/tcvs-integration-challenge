"""Tests for the fulfillment webhook flow.

This is a complete test suite for the fulfillment integration, covering:
- ShopifyFulfillment model parsing and validation
- Webhook endpoint (HMAC verification, payload parsing, chain dispatch)
- Celery tasks (resolve_order_task and fulfill_order_task)

These tests serve as a reference for how to test webhook integrations.
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.models.shopify.fulfillment import ShopifyFulfillment
from src.utils.shopify_hmac import compute_hmac


# -----------------------------------------------------------------------------
# Test Data
# -----------------------------------------------------------------------------

SAMPLE_FULFILLMENT: dict[str, Any] = {
    "id": 4567890123,
    "order_id": 5551234567890,
    "status": "success",
    "tracking_number": "1Z999AA10123456784",
    "tracking_company": "UPS",
    "tracking_url": "https://www.ups.com/track?tracknum=1Z999AA10123456784",
    "created_at": "2026-01-15T10:30:00-06:00",
}

SAMPLE_FULFILLMENT_MINIMAL: dict[str, Any] = {
    "id": 4567890124,
    "order_id": 5551234567891,
    "status": "success",
    "created_at": "2026-01-15T11:00:00-06:00",
}

WEBHOOK_SECRET = "test_webhook_secret_for_hmac"


# -----------------------------------------------------------------------------
# ShopifyFulfillment Model Tests
# -----------------------------------------------------------------------------


class TestShopifyFulfillment:
    """Tests for the ShopifyFulfillment Pydantic model."""

    def test_parse_full_fulfillment(self) -> None:
        """Test parsing a fulfillment with all fields."""
        fulfillment = ShopifyFulfillment.model_validate(SAMPLE_FULFILLMENT)

        assert fulfillment.id == 4567890123
        assert fulfillment.order_id == 5551234567890
        assert fulfillment.status == "success"
        assert fulfillment.tracking_number == "1Z999AA10123456784"
        assert fulfillment.tracking_company == "UPS"
        assert fulfillment.tracking_url is not None
        assert fulfillment.created_at == "2026-01-15T10:30:00-06:00"

    def test_parse_minimal_fulfillment(self) -> None:
        """Test parsing a fulfillment without optional tracking fields."""
        fulfillment = ShopifyFulfillment.model_validate(SAMPLE_FULFILLMENT_MINIMAL)

        assert fulfillment.id == 4567890124
        assert fulfillment.order_id == 5551234567891
        assert fulfillment.tracking_number is None
        assert fulfillment.tracking_company is None
        assert fulfillment.tracking_url is None

    def test_model_dump_round_trip(self) -> None:
        """Test that model_dump produces a dict suitable for Celery task args."""
        fulfillment = ShopifyFulfillment.model_validate(SAMPLE_FULFILLMENT)
        dumped = fulfillment.model_dump()

        assert dumped["id"] == 4567890123
        assert dumped["order_id"] == 5551234567890
        assert dumped["tracking_number"] == "1Z999AA10123456784"

    def test_extra_fields_ignored(self) -> None:
        """Test that extra Shopify fields don't cause validation errors."""
        data = {
            **SAMPLE_FULFILLMENT,
            "admin_graphql_api_id": "gid://shopify/Fulfillment/4567890123",
            "line_items": [{"id": 1}],
            "receipt": {},
        }
        fulfillment = ShopifyFulfillment.model_validate(data)
        assert fulfillment.id == 4567890123


# -----------------------------------------------------------------------------
# Webhook Endpoint Tests
# -----------------------------------------------------------------------------


class TestFulfillmentWebhook:
    """Tests for the POST /webhooks/fulfillments/create endpoint.

    These tests use FastAPI's TestClient to simulate real HTTP requests,
    including HMAC signature verification.
    """

    @pytest.fixture
    def client(self) -> TestClient:
        """Create a test client for the FastAPI app."""
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def mock_settings(self) -> Any:
        """Configure a known webhook secret for HMAC testing."""
        with patch("src.utils.shopify_hmac.get_settings") as mock:
            mock.return_value = MagicMock(
                shopify_webhook_secret=WEBHOOK_SECRET,
                environment="production",
            )
            yield mock

    @pytest.fixture(autouse=True)
    def mock_fulfillment_chain(self) -> Any:
        """Mock the fulfillment chain to prevent actual task dispatch."""
        with patch("src.routes.webhooks.build_fulfillment_chain") as mock:
            mock_chain = MagicMock()
            mock.return_value = mock_chain
            yield mock

    def _signed_request(
        self, client: TestClient, payload: dict[str, Any]
    ) -> Any:
        """Helper to send a properly signed webhook request."""
        body = json.dumps(payload).encode()
        signature = compute_hmac(body, WEBHOOK_SECRET)

        return client.post(
            "/webhooks/fulfillments/create",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Hmac-Sha256": signature,
            },
        )

    def test_valid_fulfillment_returns_queued(
        self, client: TestClient, mock_fulfillment_chain: MagicMock
    ) -> None:
        """Test that a valid, signed fulfillment webhook returns 200 with queued status."""
        response = self._signed_request(client, SAMPLE_FULFILLMENT)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["fulfillment_id"] == SAMPLE_FULFILLMENT["id"]

    def test_valid_fulfillment_dispatches_chain(
        self, client: TestClient, mock_fulfillment_chain: MagicMock
    ) -> None:
        """Test that the webhook dispatches a Celery chain with parsed data."""
        self._signed_request(client, SAMPLE_FULFILLMENT)

        mock_fulfillment_chain.assert_called_once()
        chain_arg = mock_fulfillment_chain.call_args[0][0]
        assert chain_arg["id"] == SAMPLE_FULFILLMENT["id"]
        assert chain_arg["order_id"] == SAMPLE_FULFILLMENT["order_id"]
        assert chain_arg["tracking_number"] == "1Z999AA10123456784"

        # Verify apply_async was called on the chain
        mock_fulfillment_chain.return_value.apply_async.assert_called_once()

    def test_missing_hmac_returns_401(self, client: TestClient) -> None:
        """Test that a request without HMAC signature is rejected."""
        response = client.post(
            "/webhooks/fulfillments/create",
            content=json.dumps(SAMPLE_FULFILLMENT).encode(),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 401

    def test_invalid_hmac_returns_401(self, client: TestClient) -> None:
        """Test that a request with wrong HMAC signature is rejected."""
        response = client.post(
            "/webhooks/fulfillments/create",
            content=json.dumps(SAMPLE_FULFILLMENT).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Hmac-Sha256": "invalid_signature_here",
            },
        )

        assert response.status_code == 401

    def test_invalid_json_returns_400(self, client: TestClient) -> None:
        """Test that an invalid JSON payload returns 400."""
        body = b"not valid json"
        signature = compute_hmac(body, WEBHOOK_SECRET)

        response = client.post(
            "/webhooks/fulfillments/create",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Hmac-Sha256": signature,
            },
        )

        assert response.status_code == 400

    def test_missing_required_fields_returns_400(self, client: TestClient) -> None:
        """Test that a payload missing required fields returns 400."""
        incomplete = {"id": 123}  # Missing order_id, status, created_at
        response = self._signed_request(client, incomplete)

        assert response.status_code == 400

    def test_minimal_fulfillment_succeeds(
        self, client: TestClient, mock_fulfillment_chain: MagicMock
    ) -> None:
        """Test that a fulfillment without tracking info is accepted."""
        response = self._signed_request(client, SAMPLE_FULFILLMENT_MINIMAL)

        assert response.status_code == 200
        mock_fulfillment_chain.assert_called_once()


# -----------------------------------------------------------------------------
# Celery Task Tests
# -----------------------------------------------------------------------------


class TestResolveOrderTask:
    """Tests for the resolve_order_task Celery task."""

    @pytest.fixture(autouse=True)
    def mock_tecovasuite(self) -> Any:
        """Mock the TecovaSuiteClient used by the task."""
        with patch("src.tasks.process_fulfillment.TecovaSuiteClient") as mock_class:
            mock_instance = MagicMock()
            mock_instance.get_sales_order_by_external_id.return_value = {
                "internal_id": "5001",
                "external_id": "5551234567890",
                "status": "pendingFulfillment",
            }
            mock_class.return_value = mock_instance
            yield mock_instance

    def test_resolves_external_id_to_internal_id(
        self, mock_tecovasuite: MagicMock
    ) -> None:
        """Test that the task looks up the order and adds internal_id."""
        from src.tasks.process_fulfillment import resolve_order_task

        fulfillment_data = {
            "id": 4567890123,
            "order_id": 5551234567890,
            "tracking_number": "1Z999AA10123456784",
            "tracking_company": "UPS",
        }

        result = resolve_order_task(fulfillment_data)

        mock_tecovasuite.get_sales_order_by_external_id.assert_called_once_with(
            "5551234567890"
        )
        assert result["internal_id"] == "5001"
        # Original data is preserved
        assert result["order_id"] == 5551234567890
        assert result["tracking_number"] == "1Z999AA10123456784"

    def test_converts_order_id_to_string(
        self, mock_tecovasuite: MagicMock
    ) -> None:
        """Test that numeric order_id is converted to string for the lookup."""
        from src.tasks.process_fulfillment import resolve_order_task

        fulfillment_data = {"id": 1, "order_id": 9999999999999}

        resolve_order_task(fulfillment_data)

        call_args = mock_tecovasuite.get_sales_order_by_external_id.call_args
        assert call_args[0][0] == "9999999999999"
        assert isinstance(call_args[0][0], str)


class TestFulfillOrderTask:
    """Tests for the fulfill_order_task Celery task."""

    @pytest.fixture(autouse=True)
    def mock_tecovasuite(self) -> Any:
        """Mock the TecovaSuiteClient used by the task."""
        with patch("src.tasks.process_fulfillment.TecovaSuiteClient") as mock_class:
            mock_instance = MagicMock()
            mock_instance.fulfill_sales_order.return_value = {
                "internal_id": "5001",
                "status": "fulfilled",
                "fulfillment_status": "fulfilled",
            }
            mock_class.return_value = mock_instance
            yield mock_instance

    def test_fulfills_with_tracking_info(
        self, mock_tecovasuite: MagicMock
    ) -> None:
        """Test that the task calls fulfill with internal_id and tracking."""
        from src.tasks.process_fulfillment import fulfill_order_task

        fulfillment_data = {
            "id": 4567890123,
            "order_id": 5551234567890,
            "internal_id": "5001",
            "tracking_number": "1Z999AA10123456784",
            "tracking_company": "UPS",
        }

        result = fulfill_order_task(fulfillment_data)

        mock_tecovasuite.fulfill_sales_order.assert_called_once_with(
            internal_id="5001",
            tracking_number="1Z999AA10123456784",
            tracking_company="UPS",
        )
        assert result["status"] == "fulfilled"
        assert result["fulfillment_id"] == 4567890123
        assert result["internal_id"] == "5001"

    def test_fulfills_without_tracking_info(
        self, mock_tecovasuite: MagicMock
    ) -> None:
        """Test that the task works without tracking information."""
        from src.tasks.process_fulfillment import fulfill_order_task

        fulfillment_data = {
            "id": 4567890124,
            "order_id": 5551234567891,
            "internal_id": "5002",
        }

        result = fulfill_order_task(fulfillment_data)

        mock_tecovasuite.fulfill_sales_order.assert_called_once_with(
            internal_id="5002",
            tracking_number=None,
            tracking_company=None,
        )
        assert result["status"] == "fulfilled"

    def test_returns_complete_result(
        self, mock_tecovasuite: MagicMock
    ) -> None:
        """Test that the task returns all expected fields."""
        from src.tasks.process_fulfillment import fulfill_order_task

        fulfillment_data = {
            "id": 4567890123,
            "order_id": 5551234567890,
            "internal_id": "5001",
            "tracking_number": "TRACK123",
            "tracking_company": "FedEx",
        }

        result = fulfill_order_task(fulfillment_data)

        assert result["status"] == "fulfilled"
        assert result["fulfillment_id"] == 4567890123
        assert result["order_id"] == 5551234567890
        assert result["internal_id"] == "5001"
        assert "tecovasuite_response" in result


# -----------------------------------------------------------------------------
# Chain Builder Tests
# -----------------------------------------------------------------------------


class TestBuildFulfillmentChain:
    """Tests for the build_fulfillment_chain helper."""

    def test_builds_chain_with_two_tasks(self) -> None:
        """Test that the chain contains resolve and fulfill tasks."""
        from src.tasks.process_fulfillment import build_fulfillment_chain

        fulfillment_data = {
            "id": 4567890123,
            "order_id": 5551234567890,
        }

        workflow = build_fulfillment_chain(fulfillment_data)

        # A Celery chain should have tasks attribute
        assert len(workflow.tasks) == 2
