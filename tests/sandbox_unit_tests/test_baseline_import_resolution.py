"""Unit tests for the baseline_import_resolution sandbox."""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "sandboxes", "baseline_import_resolution"),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from pkg.metrics import StaticScoreProvider, process as risk_process
from pkg.utils import sanitize_user_id, helper, process as util_process, caller
from pkg.formatter import DefaultPayloadFormatter
from pkg.service import UserService


# ---------------------------------------------------------------------------
# StaticScoreProvider
# ---------------------------------------------------------------------------

def test_static_score_provider_returns_int():
    provider = StaticScoreProvider()
    assert isinstance(provider.score_for("u-123"), int)


def test_static_score_provider_positive():
    provider = StaticScoreProvider()
    assert provider.score_for("u-123") > 0


def test_static_score_provider_scales_with_id_length():
    provider = StaticScoreProvider()
    short = provider.score_for("u-1")
    long_ = provider.score_for("u-very-long-id")
    assert long_ > short


# ---------------------------------------------------------------------------
# pkg.metrics.process (risk_process)
# ---------------------------------------------------------------------------

def test_risk_process_returns_two():
    assert risk_process() == 2


# ---------------------------------------------------------------------------
# pkg.utils
# ---------------------------------------------------------------------------

def test_sanitize_user_id_strips():
    assert sanitize_user_id("  u-123  ") == "u-123"


def test_sanitize_user_id_lowercases():
    assert sanitize_user_id("U-ABC") == "u-abc"


def test_helper_returns_ok():
    assert helper() == "ok"


def test_util_process_returns_one():
    assert util_process() == 1


def test_caller_delegates_to_process():
    assert caller() == util_process()


# ---------------------------------------------------------------------------
# DefaultPayloadFormatter
# ---------------------------------------------------------------------------

def test_formatter_output_keys():
    formatter = DefaultPayloadFormatter()
    payload = {"user_id": "u-1", "status": "ok", "score": 42, "risk": 2}
    result = formatter.format(payload)
    assert set(result.keys()) == {"id", "status", "score", "label", "risk"}


def test_formatter_id_maps_from_user_id():
    formatter = DefaultPayloadFormatter()
    payload = {"user_id": "u-999", "status": "ok", "score": 10, "risk": 2}
    result = formatter.format(payload)
    assert result["id"] == "u-999"


def test_formatter_label_format():
    formatter = DefaultPayloadFormatter()
    payload = {"user_id": "u-1", "status": "ok", "score": 5, "risk": 1}
    result = formatter.format(payload)
    assert result["label"] == "ok-5"


def test_formatter_score_is_int():
    formatter = DefaultPayloadFormatter()
    payload = {"user_id": "u-1", "status": "ok", "score": "42", "risk": "2"}
    result = formatter.format(payload)
    assert isinstance(result["score"], int)


# ---------------------------------------------------------------------------
# UserService
# ---------------------------------------------------------------------------

def test_user_service_run_returns_dict():
    service = UserService()
    result = service.run("u-123")
    assert isinstance(result, dict)


def test_user_service_run_has_id():
    service = UserService()
    result = service.run("u-123")
    assert "id" in result


def test_user_service_run_sanitizes_id():
    service = UserService()
    result = service.run("  U-ABC  ")
    assert result["id"] == "u-abc"


def test_user_service_run_status_field():
    service = UserService()
    result = service.run("u-123")
    assert result["status"] == "ok"


def test_user_service_run_score_is_int():
    service = UserService()
    result = service.run("u-123")
    assert isinstance(result["score"], int)
