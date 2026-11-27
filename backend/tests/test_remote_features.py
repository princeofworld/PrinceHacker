"""Consent, ownership, and delivery tests for remote safety and WebRTC APIs."""
import importlib
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("FAMILYGUARD_DB_PATH", str(tmp_path / "remote-features.sqlite3"))
    monkeypatch.delenv("RENDER_DISK_PATH", raising=False)
    import backend.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        yield main, client


def register(client):
    email = f"{uuid.uuid4().hex}@gmail.com"
    response = client.post("/auth/register", json={"email": email, "password": "Correct-Horse-42!"})
    assert response.status_code == 200, response.text
    data = response.json()
    return email, data, {"Authorization": f"Bearer {data['idToken']}"}


def register_device(client, headers):
    device_id = uuid.uuid4().hex
    response = client.post(
        "/devices/register",
        headers=headers,
        json={"deviceId": device_id, "deviceName": "Test Android", "platform": "android"},
    )
    assert response.status_code == 200, response.text
    return device_id


def test_latest_app_version_matches_current_android_release(api, monkeypatch):
    _, client = api
    monkeypatch.delenv("APP_VERSION_NAME", raising=False)
    monkeypatch.delenv("APP_VERSION_CODE", raising=False)
    response = client.get("/app/latest-version")
    assert response.status_code == 200
    assert response.json()["versionName"] == "1.3.4"
    assert response.json()["versionCode"] == 9
    assert response.json()["minimumVersionCode"] <= 9


def test_remote_alert_ring_and_local_lock_request_are_owner_authenticated(api):
    _, client = api
    _, _, owner_headers = register(client)
    _, _, other_headers = register(client)
    device_id = register_device(client, owner_headers)

    forbidden = client.post(
        f"/api/devices/{device_id}/commands",
        headers=other_headers,
        json={"kind": "ring", "message": "hello"},
    )
    assert forbidden.status_code == 404

    missing_local_acceptance = client.post(
        f"/api/devices/{device_id}/commands",
        headers=owner_headers,
        json={"kind": "in_app_lock_request", "message": "Pause", "requiresLocalAcceptance": False},
    )
    assert missing_local_acceptance.status_code == 400

    invalid_ttl = client.post(
        f"/api/devices/{device_id}/commands",
        headers=owner_headers,
        json={"kind": "alert", "ttlSeconds": "not-a-number"},
    )
    assert invalid_ttl.status_code == 400

    alert = client.post(
        f"/api/devices/{device_id}/commands",
        headers=owner_headers,
        json={"kind": "alert", "message": "Please check in"},
    )
    assert alert.status_code == 200, alert.text
    command_id = alert.json()["commandId"]

    polled = client.get(f"/api/devices/{device_id}/commands", headers=owner_headers)
    assert polled.status_code == 200
    assert any(item["commandId"] == command_id and item["kind"] == "alert"
               for item in polled.json()["commands"])

    ack = client.post(
        f"/api/devices/{device_id}/commands/{command_id}/ack",
        headers=owner_headers,
        json={"status": "completed"},
    )
    assert ack.status_code == 200

    lock = client.post(
        f"/api/devices/{device_id}/commands",
        headers=owner_headers,
        json={"kind": "in_app_lock_request", "message": "Optional app pause",
              "requiresLocalAcceptance": True},
    )
    assert lock.status_code == 200
    lock_id = lock.json()["commandId"]
    lock_poll = client.get(f"/api/devices/{device_id}/commands", headers=owner_headers)
    assert any(item["commandId"] == lock_id for item in lock_poll.json()["commands"])

    declined = client.post(
        f"/api/devices/{device_id}/commands/{lock_id}/ack",
        headers=owner_headers,
        json={"status": "declined"},
    )
    assert declined.status_code == 200


def test_webrtc_requires_server_side_metered_secrets(api, monkeypatch):
    _, client = api
    _, _, headers = register(client)
    device_id = register_device(client, headers)
    monkeypatch.delenv("METERED_DOMAIN", raising=False)
    monkeypatch.delenv("METERED_SECRET_KEY", raising=False)

    response = client.post(
        "/api/webrtc/sessions",
        headers=headers,
        json={"deviceId": device_id, "consentAccepted": True},
    )
    assert response.status_code == 503


def test_webrtc_session_requires_explicit_consent_and_owned_device(api, monkeypatch):
    _, client = api
    _, _, owner_headers = register(client)
    _, _, other_headers = register(client)
    device_id = register_device(client, owner_headers)
    monkeypatch.setenv("METERED_DOMAIN", "example.metered.test")
    monkeypatch.setenv("METERED_SECRET_KEY", "test-secret-never-committed")

    no_consent = client.post(
        "/api/webrtc/sessions",
        headers=owner_headers,
        json={"deviceId": device_id, "consentAccepted": False},
    )
    assert no_consent.status_code == 400

    wrong_owner = client.post(
        "/api/webrtc/sessions",
        headers=other_headers,
        json={"deviceId": device_id, "consentAccepted": True},
    )
    assert wrong_owner.status_code == 404


def test_webrtc_signaling_is_peer_scoped_and_ice_is_available_to_owner(api, monkeypatch):
    main, client = api
    _, _, owner_headers = register(client)
    _, _, other_headers = register(client)
    device_id = register_device(client, owner_headers)
    monkeypatch.setenv("METERED_DOMAIN", "example.metered.test")
    monkeypatch.setenv("METERED_SECRET_KEY", "test-secret-never-committed")

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            import json
            return json.dumps(self.payload).encode()

    def fake_urlopen(request, timeout):
        url = request.full_url if hasattr(request, "full_url") else request
        if "/turn/credential?" in url:
            return FakeResponse({"apiKey": "temporary-test-key"})
        return FakeResponse([{"urls": ["stun:stun.example.test:3478"]},
                             {"urls": ["turn:turn.example.test:3478"], "username": "temp-user",
                              "credential": "temp-password"}])

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)
    created = client.post("/api/webrtc/sessions", headers=owner_headers,
                          json={"deviceId": device_id, "consentAccepted": True})
    assert created.status_code == 200, created.text
    session_id = created.json()["sessionId"]
    assert created.json()["iceServers"]

    listed = client.get("/api/webrtc/sessions", headers=owner_headers)
    assert listed.status_code == 200
    assert listed.json()["sessions"][0]["sessionId"] == session_id
    assert listed.json()["sessions"][0]["iceServers"]

    missing_sender = client.post(f"/api/webrtc/{session_id}/signals", headers=owner_headers,
                                 json={"kind": "offer", "payload": {"type": "offer", "sdp": "v=0"}})
    assert missing_sender.status_code == 400

    offer = client.post(f"/api/webrtc/{session_id}/signals", headers=owner_headers,
                        json={"kind": "offer", "senderId": "android-phone",
                              "payload": {"type": "offer", "sdp": "v=0"}})
    assert offer.status_code == 200
    own = client.get(f"/api/webrtc/{session_id}/signals?sender_id=android-phone",
                     headers=owner_headers)
    assert own.status_code == 200
    assert own.json()["signals"] == []
    viewer = client.get(f"/api/webrtc/{session_id}/signals?sender_id=dashboard-browser",
                        headers=owner_headers)
    assert viewer.status_code == 200
    assert viewer.json()["signals"][0]["kind"] == "offer"
    assert viewer.json()["signals"][0]["signalId"]

    answer = client.post(f"/api/webrtc/{session_id}/signals", headers=owner_headers,
                         json={"kind": "answer", "senderId": "dashboard-browser",
                               "payload": {"type": "answer", "sdp": "v=0"}})
    assert answer.status_code == 200
    received = client.get(f"/api/webrtc/{session_id}/signals?sender_id=android-phone",
                          headers=owner_headers)
    assert [signal["kind"] for signal in received.json()["signals"]] == ["answer"]

    forbidden = client.get(f"/api/webrtc/{session_id}/signals?sender_id=intruder",
                           headers=other_headers)
    assert forbidden.status_code == 404

    ended = client.post(f"/api/webrtc/{session_id}/signals", headers=owner_headers,
                        json={"kind": "end", "senderId": "android-phone", "payload": {}})
    assert ended.status_code == 200
    after_end = client.get(f"/api/webrtc/{session_id}/signals?sender_id=dashboard-browser",
                           headers=owner_headers)
    assert after_end.status_code == 404


def test_manual_upi_utr_review_activates_premium_for_30_days(api, monkeypatch):
    _, client = api
    _, user, headers = register(client)
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", "test-admin")
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", "test-admin-password-strong-123")

    config = client.get("/payment/upi-config")
    assert config.status_code == 200
    assert config.json()["amount"] == 99900

    submitted = client.post("/payment/submit-utr", headers=headers,
                            json={"utr": "123456789012"})
    assert submitted.status_code == 200, submitted.text
    payment_id = submitted.json()["paymentId"]

    duplicate = client.post("/payment/submit-utr", headers=headers,
                            json={"utr": "123456789012"})
    assert duplicate.status_code == 409

    pending = client.get("/payment/my-utr", headers=headers)
    assert pending.status_code == 200
    assert pending.json()["payments"][0]["status"] == "pending"

    assert client.get("/admin/api/payments").status_code == 401
    admin_headers = {"Authorization": "Basic " + __import__("base64").b64encode(
        b"test-admin:test-admin-password-strong-123").decode()}
    queue = client.get("/admin/api/payments?status=pending", headers=admin_headers)
    assert queue.status_code == 200
    assert any(item["id"] == payment_id for item in queue.json()["payments"])

    approved = client.post(f"/admin/api/payments/{payment_id}/approve",
                           headers=admin_headers, json={"note": "Verified in test"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved"] is True

    billing = client.get("/billing/status", headers=headers)
    assert billing.status_code == 200, billing.text
    assert billing.json().get("plan") == "premium"

    repeated = client.post(f"/admin/api/payments/{payment_id}/approve",
                           headers=admin_headers, json={"note": "Duplicate"})
    assert repeated.status_code == 409


def test_manual_upi_utr_rejection_does_not_unlock_premium(api, monkeypatch):
    _, client = api
    _, _, headers = register(client)
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", "test-admin")
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", "test-admin-password-strong-123")
    submitted = client.post("/payment/submit-utr", headers=headers,
                            json={"utr": "987654321098"})
    assert submitted.status_code == 200, submitted.text
    payment_id = submitted.json()["paymentId"]
    admin_headers = {"Authorization": "Basic " + __import__("base64").b64encode(
        b"test-admin:test-admin-password-strong-123").decode()}
    rejected = client.post(f"/admin/api/payments/{payment_id}/reject",
                           headers=admin_headers, json={"note": "Not found in statement"})
    assert rejected.status_code == 200, rejected.text
    payment = client.get("/payment/my-utr", headers=headers).json()["payments"][0]
    assert payment["status"] == "rejected"
    billing = client.get("/billing/status", headers=headers)
    assert billing.status_code == 200
    assert billing.json().get("plan") != "premium"
