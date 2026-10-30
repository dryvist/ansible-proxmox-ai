"""Exercise the deployed Hermes SDK against the disposable Hindsight API."""

import json
import os
import time
from importlib.metadata import version
from urllib.request import Request, urlopen

import pytest
from hindsight_client import Hindsight
from hindsight_client_api.exceptions import ApiException


BASE_URL = "http://127.0.0.1:8888"
BANK = "molecule-sdk-compat"
FIXTURE_KEY = "molecule-test-hindsight-api-key"


def test_pinned_client_decodes_retain_recall_and_reflect():
    assert version("hindsight-client") == os.environ["HINDSIGHT_FIXTURE_EXPECTED_CLIENT_VERSION"]
    client = Hindsight(base_url=BASE_URL, api_key=FIXTURE_KEY, timeout=120, max_attempts=1)
    retained = client.retain(
        bank_id=BANK,
        content="Alice lives in Berlin and grows orchids.",
        document_id="molecule-sdk-document",
    )
    assert retained.success is True
    assert retained.bank_id == BANK
    assert retained.items_count == 1
    assert retained.var_async is False

    recalled = client.recall(bank_id=BANK, query="Where does Alice live?")
    assert recalled.results
    assert any("Berlin" in item.text and item.id for item in recalled.results)

    reflected = client.reflect(bank_id=BANK, query="Where does Alice live?")
    assert isinstance(reflected.text, str)
    assert reflected.text.strip()


def test_pinned_client_surfaces_authentication_refusal():
    client = Hindsight(base_url=BASE_URL, api_key="invalid-fixture-key", timeout=120, max_attempts=1)
    with pytest.raises(ApiException) as exc:
        client.recall(bank_id=BANK, query="Where does Alice live?")
    assert exc.value.status in (401, 403)


def test_async_retain_completes_and_is_recallable():
    """Use the plugin's async write mode, including the worker completion gate."""
    bank = BANK + "-async"
    client = Hindsight(base_url=BASE_URL, api_key=FIXTURE_KEY, timeout=120, max_attempts=1)
    retained = client.retain(
        bank_id=bank,
        content="Alice lives in Berlin and grows orchids.",
        document_id="molecule-async-document",
        retain_async=True,
    )
    assert retained.success is True
    assert retained.var_async is True
    operation_ids = retained.operation_ids or [retained.operation_id]
    assert operation_ids and all(operation_ids)
    deadline = time.monotonic() + 120
    for operation_id in operation_ids:
        while True:
            request = Request(
                f"{BASE_URL}/v1/default/banks/{bank}/operations/{operation_id}",
                headers={"Authorization": f"Bearer {FIXTURE_KEY}"},
            )
            with urlopen(request, timeout=10) as response:
                operation = json.load(response)
            assert operation["status"] not in {"failed", "cancelled"}, operation
            if operation["status"] == "completed":
                break
            assert time.monotonic() < deadline, operation
            time.sleep(1)
    recalled = client.recall(bank_id=bank, query="Where does Alice live?")
    assert any("Berlin" in item.text and item.id for item in recalled.results)
