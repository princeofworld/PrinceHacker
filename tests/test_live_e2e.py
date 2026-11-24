"""Opt-in live E2E checks for the isolated FamilyGuard CI bot account.

All write operations are scoped to the dedicated e2e_ci_bot account and a
freshly generated device ID. The account is deleted in a finally block.
"""
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

DEFAULT_LIVE_URL = "https://princehacker.onrender.com"
BASE_URL = (
    os.getenv("LIVE_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or DEFAULT_LIVE_URL
).rstrip("/")
BOT_EMAIL = "e2e_ci_bot@princehacker.test"
TIMEOUT = (10, 30)


def request(method, path, *, token=None, expected=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(
        method, urljoin(BASE_URL + "/", path.lstrip("/")),
        headers=headers, timeout=TIMEOUT, **kwargs
    )
    if expected is not None:
        assert response.status_code in expected, (
            f"{method} {path}: expected {expected}, got "
            f"{response.status_code}: {response.text[:500]}"
        )
    assert response.status_code < 500, (
        f"{method} {path} returned server error {response.status_code}: "
        f"{response.text[:500]}"
    )
    return response


def test_live_familyguard_end_to_end():
    # Read-only deployment checks first.
    health = request("GET", "/health", expected={200}).json()
    assert health.get("status") == "ok", health
    assert health.get("databaseConnected") is True, health

    for path in ("/login", "/dashboard", "/admin"):
        page = request("GET", path, expected={200})
        assert "text/html" in page.headers.get("content-type", "").lower(), (
            f"{path} did not return HTML"
        )

    # API pages are backed by the same files; verify their canonical URLs too.
    for path in ("/login.html", "/dashboard.html", "/admin/"):
        request("GET", path, expected={200})

    password = os.getenv("LIVE_E2E_TEST_PASSWORD") or secrets.token_urlsafe(24)
    token = None
    device_id = "e2e-" + secrets.token_hex(18)
    account_created = False
    try:
        registered = request(
            "POST", "/auth/register", expected={200, 409},
            json={"email": BOT_EMAIL, "password": password},
        )
        if registered.status_code == 409:
            # A pre-existing bot is accepted only when its password is supplied
            # as a GitHub Actions secret; never guess or reset credentials.
            assert os.getenv("LIVE_E2E_TEST_PASSWORD"), (
                "CI bot already exists; set LIVE_E2E_TEST_PASSWORD as an Actions secret"
            )
        else:
            account_created = True

        logged_in = request(
            "POST", "/auth/login", expected={200},
            json={"email": BOT_EMAIL, "password": password},
        ).json()
        token = logged_in.get("idToken")
        assert token, f"Login response did not contain idToken: {logged_in}"
        profile = request("GET", "/account/profile", token=token, expected={200}).json()
        assert profile, "Authenticated profile response was empty"

        # Dashboard tab data sources: Overview, Location History, SOS Alerts,
        # SMS/Call Summary, Family Management and Premium/₹999 monthly plan.
        request("GET", "/account/profile", token=token, expected={200})
        request("GET", "/family/mine", token=token, expected={200})
        request("GET", "/devices", token=token, expected={200})
        billing = request("GET", "/billing/status", token=token, expected={200}).json()
        assert "plan" in billing or "status" in billing, billing
        billing_config = request("GET", "/billing/config", token=token, expected={200}).json()
        assert billing_config, "Premium billing configuration was empty"
        upi = request("GET", "/payment/upi-config", token=token, expected={200}).json()
        assert upi.get("amount") in (None, 99900), upi

        # Register a uniquely named CI-only device.
        request(
            "POST", "/devices/register", token=token, expected={200},
            json={
                "deviceId": device_id,
                "deviceName": "FamilyGuard CI E2E device",
                "platform": "android",
            },
        )

        # Enable only the test device's location and aggregate-summary consent.
        enabled = request(
            "POST", "/api/privacy/consent", token=token, expected={200},
            json={
                "deviceId": device_id,
                "location": True,
                "battery": False,
                "summaries": True,
                "sms_summary": True,
            },
        ).json()
        assert enabled.get("location") is True and enabled.get("sms_summary") is True, enabled

        # Invalid bearer tokens must not create SOS alerts.
        now = datetime.now(timezone.utc).isoformat()
        sos_payload = {
            "message": "Automated CI test alert - no emergency",
            "latitude": 0.0,
            "longitude": 0.0,
            "accuracyMeters": 1000,
            "capturedAt": now,
        }
        invalid = request(
            "POST", f"/api/devices/{device_id}/sos",
            headers={"Authorization": "Bearer invalid-ci-token"},
            expected={401, 403}, json=sos_payload,
        )
        assert invalid.status_code in (401, 403)

        # Valid test account can create, observe and resolve its own test alert.
        created = request(
            "POST", f"/api/devices/{device_id}/sos",
            token=token, expected={200}, json=sos_payload,
        ).json()
        alert_id = created.get("alertId")
        assert alert_id, created
        alerts = request(
            "GET", f"/api/devices/{device_id}/sos",
            token=token, expected={200},
        ).json()
        assert any(item.get("alertId") == alert_id for item in alerts.get("alerts", [])), alerts
        resolved = request(
            "POST", f"/api/devices/{device_id}/sos/{alert_id}/resolve",
            token=token, expected={200},
        ).json()
        assert resolved.get("status") == "resolved", resolved

        # Turn consent OFF for this CI device and verify Android's consent-sync
        # read returns false before attempting either upload.
        off = request(
            "POST", "/api/privacy/consent", token=token, expected={200},
            json={
                "deviceId": device_id,
                "location": False,
                "battery": False,
                "summaries": False,
                "sms_summary": False,
            },
        ).json()
        assert off.get("location") is False and off.get("sms_summary") is False, off
        sync = request(
            "GET", f"/api/privacy/consent?device_id={device_id}",
            token=token, expected={200},
        ).json()
        assert sync.get("location") is False
        assert sync.get("summaries") is False
        assert sync.get("sms_summary") is False

        blocked_location = request(
            "POST", "/location/update", token=token, expected={403},
            json={
                "latitude": 0.0,
                "longitude": 0.0,
                "accuracyMeters": 1000,
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "familyId": "ci-test-family-not-used",
                "deviceId": device_id,
            },
        )
        assert blocked_location.status_code == 403

        blocked_sms = request(
            "POST", f"/api/devices/{device_id}/sms-summary",
            token=token, expected={403},
            json={"sms_today_count": 1, "sms_safety_status": "safe"},
        )
        assert blocked_sms.status_code == 403

        # Re-enable the synthetic device solely to read its now-empty history
        # and aggregate summary; consent OFF already purged these test-device
        # fields in the backend.
        restored = request(
            "POST", "/api/privacy/consent", token=token, expected={200},
            json={
                "deviceId": device_id,
                "location": True,
                "battery": False,
                "summaries": True,
                "sms_summary": True,
            },
        ).json()
        assert restored.get("location") is True and restored.get("sms_summary") is True
        history = request(
            "GET", f"/api/devices/{device_id}/locations",
            token=token, expected={200},
        ).json()
        assert history.get("count") == 0 and history.get("locations") == [], history
        sms = request(
            "GET", f"/api/devices/{device_id}/sms-summary",
            token=token, expected={200},
        ).json()
        assert sms.get("sms_today_count") in (None, 0), sms

        # Confirm remaining dashboard sections still respond after the flow.
        request("GET", "/account/profile", token=token, expected={200})
        request("GET", "/family/mine", token=token, expected={200})
        request("GET", "/billing/status", token=token, expected={200})
    finally:
        # Delete the entire synthetic account (and its device/SOS data).
        if token:
            cleanup = request("DELETE", "/account-data", token=token, expected={200})
            assert cleanup.json().get("deleted") is True
