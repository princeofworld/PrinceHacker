"""Security regression tests for FamilyGuard auth and tenant isolation.

SQLite is always exercised. Set TEST_POSTGRES_DATABASE_URL to also run the
same suite against a disposable PostgreSQL test database.
"""
import importlib
import os
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(params=["sqlite", "postgresql"] if os.getenv("TEST_POSTGRES_DATABASE_URL") else ["sqlite"])
def api(request, monkeypatch, tmp_path):
    if request.param == "postgresql":
        monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_DATABASE_URL"])
    else:
        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("FAMILYGUARD_DB_PATH", str(tmp_path / "familyguard-test.sqlite3"))
    monkeypatch.delenv("RENDER_DISK_PATH", raising=False)
    import backend.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        yield main, client


def register(client):
    email = f"{uuid.uuid4().hex}@example.test"
    response = client.post("/auth/register", json={"email": email, "password": "Correct-Horse-42!"})
    assert response.status_code == 200, response.text
    data = response.json()
    return email, data, {"Authorization": f"Bearer {data['idToken']}"}


def test_logout_revokes_only_the_logged_out_device_session(api):
    email, first, first_headers = register(api[1])
    second = api[1].post("/auth/login", json={"email": email, "password": "Correct-Horse-42!"})
    assert second.status_code == 200, second.text
    second_headers = {"Authorization": f"Bearer {second.json()['idToken']}"}
    logout = api[1].post("/auth/logout", json={"refreshToken": first["refreshToken"]})
    assert logout.status_code == 200
    assert api[1].get("/account/profile", headers=first_headers).status_code == 401
    # Per-device logout must not invalidate another active device session.
    assert api[1].get("/account/profile", headers=second_headers).status_code == 200


def test_access_token_is_revoked_after_logout(api):
    _, data, headers = register(api[1])
    assert api[1].get("/account/profile", headers=headers).status_code == 200
    logout = api[1].post("/auth/logout", json={"refreshToken": data["refreshToken"]})
    assert logout.status_code == 200
    assert api[1].get("/account/profile", headers=headers).status_code == 401


def test_password_change_revokes_all_existing_sessions(api):
    email, data, headers = register(api[1])
    changed = api[1].post("/auth/change-password", headers=headers, json={
        "currentPassword": "Correct-Horse-42!",
        "newPassword": "New-Correct-Horse-57!",
    })
    assert changed.status_code == 200, changed.text
    assert api[1].get("/account/profile", headers=headers).status_code == 401
    login = api[1].post("/auth/login", json={"email": email, "password": "New-Correct-Horse-57!"})
    assert login.status_code == 200, login.text


def test_login_rate_limit_is_enforced(api):
    email, _, _ = register(api[1])
    for _ in range(8):
        response = api[1].post("/auth/login", json={"email": email, "password": "wrong-password"})
        assert response.status_code == 401
    limited = api[1].post("/auth/login", json={"email": email, "password": "wrong-password"})
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") == "900"


def test_invite_rate_limit_is_enforced(api):
    main, client = api
    _, data, headers = register(client)
    with main.db() as conn:
        code = f"{__import__('secrets').randbelow(1000000):06d}"
        while conn.execute("SELECT 1 FROM invites WHERE code=?", (code,)).fetchone():
            code = f"{__import__('secrets').randbelow(1000000):06d}"
    for _ in range(8):
        response = client.post("/family/join", headers=headers, json={"inviteCode": code})
        assert response.status_code == 404
    limited = client.post("/family/join", headers=headers, json={"inviteCode": code})
    assert limited.status_code == 429


def test_adversarial_cross_user_family_and_device_isolation(api):
    main, client = api
    _, owner, owner_headers = register(client)
    _, attacker, attacker_headers = register(client)
    family_id = "family-" + uuid.uuid4().hex[:18]
    device_id = "device-" + uuid.uuid4().hex[:18]
    now = __import__("time").time()
    with main.db() as conn:
        conn.execute("INSERT INTO families(id,name,owner_uid,created) VALUES(?,?,?,?)",
                     (family_id, "Private family", owner["localId"], now))
        conn.execute("INSERT INTO members(family_id,uid,role,consent) VALUES(?,?,?,1)",
                     (family_id, owner["localId"], "owner"))
    registered = client.post("/devices/register", headers=owner_headers, json={
        "deviceId": device_id, "deviceName": "Owner phone", "platform": "android"
    })
    assert registered.status_code == 200, registered.text

    # The unrelated account cannot enumerate family devices or family locations.
    assert client.get("/devices", params={"familyId": family_id}, headers=attacker_headers).status_code == 403
    assert client.get("/location/family", params={"familyId": family_id}, headers=attacker_headers).status_code == 403
    # Nor can it delete a device by guessing/learning its identifier.
    assert client.delete(f"/devices/{device_id}", headers=attacker_headers).status_code == 404
    # Owner can still see and manage their own device.
    own = client.get("/devices", headers=owner_headers)
    assert own.status_code == 200
    assert any(item["deviceId"] == device_id for item in own.json()["devices"])
