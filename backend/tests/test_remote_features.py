"""Consent, ownership, and delivery tests for remote safety and WebRTC APIs."""
import uuid


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
