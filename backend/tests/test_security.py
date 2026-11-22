"""Security regression tests for FamilyGuard auth and tenant isolation.

SQLite is always exercised. Set TEST_POSTGRES_DATABASE_URL to also run the
same suite against a disposable PostgreSQL test database.
"""
import importlib
import os
import sys
import uuid
from pathlib import Path

# Ensure the repository root is importable when pytest selects this nested file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
    email = f"{uuid.uuid4().hex}@gmail.com"
    response = client.post("/auth/register", json={"email": email, "password": "Correct-Horse-42!"})
    assert response.status_code == 200, response.text
    data = response.json()
    return email, data, {"Authorization": f"Bearer {data['idToken']}"}




def admin_auth():
    return ("familyguard-test-admin", "test-admin-password-at-least-16")


def submit_utr(client, headers, utr="123456789012"):
    return client.post("/payment/submit-utr", headers=headers, json={"utr": utr})


def test_upi_utr_end_to_end_manual_approval_activates_30_day_premium(api, monkeypatch):
    _, client = api
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", admin_auth()[0])
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", admin_auth()[1])
    monkeypatch.setenv("ADMIN_UPI_ID", "familyguard-test@upi")
    _, user, user_headers = register(client)

    config = client.get("/payment/upi-config")
    assert config.status_code == 200
    assert config.json()["amount"] == 99900
    assert config.json()["currency"] == "INR"
    assert config.json()["mode"] == "manual_review"

    submitted = submit_utr(client, user_headers, "123456789012")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending"
    assert "not active" in submitted.json()["message"].lower()
    pending_billing = client.get("/billing/status", headers=user_headers).json()
    assert pending_billing["plan"] != "premium"
    assert pending_billing["expiresAt"] is None

    duplicate = submit_utr(client, user_headers, "123456789012")
    assert duplicate.status_code == 409

    # A different account cannot use its bearer token as admin credentials.
    _, _, other_headers = register(client)
    blocked = client.post(
        f"/admin/api/payments/{submitted.json()['paymentId']}/approve",
        headers=other_headers,
        json={"note": "attempted self approval"},
    )
    assert blocked.status_code == 401

    pending = client.get("/admin/api/payments", auth=admin_auth())
    assert pending.status_code == 200
    payment = next(p for p in pending.json()["payments"] if p["id"] == submitted.json()["paymentId"])
    assert payment["amount_paise"] == 99900
    assert payment["status"] == "pending"

    approved = client.post(
        f"/admin/api/payments/{submitted.json()['paymentId']}/approve",
        auth=admin_auth(),
        json={"note": "Verified against bank statement"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved"] is True

    billing = client.get("/billing/status", headers=user_headers).json()
    assert billing["plan"] == "premium"
    assert billing["premium"] is True
    now = __import__("time").time()
    assert billing["expiresAt"] > now + 29 * 24 * 60 * 60
    assert billing["expiresAt"] <= now + 30 * 24 * 60 * 60 + 2

    history = client.get("/payment/my-utr", headers=user_headers).json()["payments"]
    assert history[0]["status"] == "approved"
    assert history[0]["review_note"] == "Verified against bank statement"

    # A reviewed request is immutable: it cannot be approved a second time.
    repeated = client.post(
        f"/admin/api/payments/{submitted.json()['paymentId']}/approve",
        auth=admin_auth(),
        json={"note": "second approval"},
    )
    assert repeated.status_code == 409


def test_utr_rejection_reason_is_visible_and_does_not_activate_premium(api, monkeypatch):
    _, client = api
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", admin_auth()[0])
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", admin_auth()[1])
    _, _, headers = register(client)
    submitted = submit_utr(client, headers, "987654321098")
    assert submitted.status_code == 200

    rejected = client.post(
        f"/admin/api/payments/{submitted.json()['paymentId']}/reject",
        auth=admin_auth(),
        json={"note": "UTR bank statement se match nahi hua"},
    )
    assert rejected.status_code == 200, rejected.text
    history = client.get("/payment/my-utr", headers=headers).json()["payments"]
    assert history[0]["status"] == "rejected"
    assert history[0]["review_note"] == "UTR bank statement se match nahi hua"
    billing = client.get("/billing/status", headers=headers).json()
    assert billing["plan"] != "premium"
    assert billing["expiresAt"] is None


def test_admin_payment_routes_require_configured_admin_credentials(api, monkeypatch):
    _, client = api
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", admin_auth()[0])
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", admin_auth()[1])
    assert client.get("/admin/api/payments").status_code == 401
    assert client.get("/admin/api/payments", auth=admin_auth()).status_code == 200


def test_utr_validation_rejects_non_12_digit_values(api):
    _, client = api
    _, _, headers = register(client)
    for value in ("123", "abcdefghijkl", "12345678901", "1234567890123"):
        response = client.post("/payment/submit-utr", headers=headers, json={"utr": value})
        assert response.status_code == 422



def test_account_deletion_removes_personal_records_and_transfers_owned_family(api):
    main, client = api
    _, owner, owner_headers = register(client)
    _, member, member_headers = register(client)

    family = client.post("/family/create", headers=owner_headers, json={"displayName": "Shared family"})
    assert family.status_code == 200, family.text
    family_id = family.json()["familyId"]
    joined = client.post("/family/join", headers=member_headers, json={"inviteCode": family.json()["inviteCode"]})
    assert joined.status_code == 200, joined.text

    submitted = submit_utr(client, owner_headers, "111222333444")
    assert submitted.status_code == 200, submitted.text

    deleted = client.delete("/account-data", headers=owner_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["billingManagedBy"] == "manual_upi"
    assert client.get("/account/profile", headers=owner_headers).status_code == 401

    remaining = client.get("/family/mine", headers=member_headers)
    assert remaining.status_code == 200, remaining.text
    family_after = next(f for f in remaining.json()["families"] if f["familyId"] == family_id)
    assert family_after["ownerUid"] == member["localId"]
    assert family_after["myRole"] == "owner"

    with main.db() as conn:
        assert conn.execute("SELECT 1 FROM users WHERE uid=?", (owner["localId"],)).fetchone() is None
        assert conn.execute("SELECT 1 FROM utr_payments WHERE id=?", (submitted.json()["paymentId"],)).fetchone() is None


def test_public_privacy_and_terms_documents_are_served(api):
    _, client = api
    privacy_response = client.get("/PRIVACY_POLICY.md")
    terms_response = client.get("/TERMS_OF_SERVICE.md")
    assert privacy_response.status_code == 200
    assert "foreground service" in privacy_response.text.lower()
    assert terms_response.status_code == 200
    assert "direct upi" in terms_response.text.lower()




def test_encrypted_backup_download_and_restore_round_trip(api, monkeypatch):
    main, client = api
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", admin_auth()[0])
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", admin_auth()[1])
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "ab" * 32)
    _, user, user_headers = register(client)

    denied = client.get("/admin/api/backup")
    assert denied.status_code == 401

    downloaded = client.get("/admin/api/backup", auth=admin_auth())
    assert downloaded.status_code == 200
    backup = downloaded.content
    assert backup.startswith(main.BACKUP_MAGIC)
    assert user["localId"].encode() not in backup
    assert b"password_hash" not in backup

    with main.db() as conn:
        users_before = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        conn.execute("DELETE FROM users WHERE uid=?", (user["localId"],))
    assert client.get("/account/profile", headers=user_headers).status_code == 401

    with pytest.raises(Exception):
        main.restore_backup_blob(backup, confirm_overwrite=False)
    restored = main.restore_backup_blob(backup, confirm_overwrite=True)
    assert restored["restored"] is True
    assert restored["tables"]["users"] == users_before
    assert client.get("/account/profile", headers=user_headers).status_code == 200


def test_backup_requires_valid_encryption_key(api, monkeypatch):
    main, client = api
    monkeypatch.setenv("ADMIN_PANEL_USERNAME", admin_auth()[0])
    monkeypatch.setenv("ADMIN_PANEL_PASSWORD", admin_auth()[1])
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    response = client.get("/admin/api/backup", auth=admin_auth())
    assert response.status_code == 503





def test_razorpay_recurring_routes_are_disabled_by_default(api, monkeypatch):
    _, client = api
    monkeypatch.delenv("RAZORPAY_ENABLED", raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "test-key")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")
    monkeypatch.setenv("RAZORPAY_PLAN_ID", "test-plan")
    _, _, headers = register(client)

    config = client.get("/billing/config")
    assert config.status_code == 200
    assert config.json()["provider"] == "manual_upi"
    assert config.json()["configured"] is False

    create = client.post("/payment/create-order", headers=headers)
    assert create.status_code == 503
    verify = client.post("/payment/verify", headers=headers, json={
        "razorpay_payment_id": "pay_test",
        "razorpay_subscription_id": "sub_test",
        "razorpay_signature": "invalid",
    })
    assert verify.status_code == 503
    webhook = client.post("/payment/webhook", content=b"{}")
    assert webhook.status_code == 503


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
