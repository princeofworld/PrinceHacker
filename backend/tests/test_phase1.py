"""Phase 1 backend regression tests for SQLite and PostgreSQL.

The api fixture is shared with test_security.py so both backends run when
TEST_POSTGRES_DATABASE_URL is configured.
"""
from datetime import datetime, timezone
import sqlite3
import uuid

from test_security import api, register


def _registered_device(client, headers):
    device_id = "device-" + uuid.uuid4().hex[:18]
    response = client.post("/devices/register", headers=headers, json={
        "deviceId": device_id, "deviceName": "Phase 1 test phone", "platform": "android"
    })
    assert response.status_code == 200, response.text
    return device_id


def test_phase1_migrations_are_idempotent_and_preserve_payment_and_summary_data(api):
    main, client = api
    _, user, headers = register(client)
    device_id = _registered_device(client, headers)
    assert client.post("/api/privacy/consent", headers=headers, json={
        "deviceId": device_id, "summaries": True, "sms_summary": True
    }).status_code == 200
    # The API intentionally only enables SMS summary via the explicit consent endpoint below.
    with main.db() as conn:
        conn.execute("UPDATE devices SET today_calls_count=7,contacts_count=21,sms_today_count=4,sms_safety_status='review' WHERE device_id=?", (device_id,))
        conn.execute("INSERT INTO utr_payments(id,uid,utr,amount_paise,status,created) VALUES(?,?,?,?,?,?)",
                     ("payment-"+uuid.uuid4().hex, user["localId"], uuid.uuid4().hex[:12], 99900, "pending", __import__("time").time()))
    main.init_db()
    main.init_db()
    with main.db() as conn:
        row=conn.execute("SELECT today_calls_count,contacts_count,sms_today_count,sms_safety_status FROM devices WHERE device_id=?", (device_id,)).fetchone()
        payment=conn.execute("SELECT amount_paise,status FROM utr_payments WHERE uid=?", (user["localId"],)).fetchone()
    assert tuple(row[k] for k in ("today_calls_count","contacts_count","sms_today_count","sms_safety_status")) == (7,21,4,"review")
    assert payment["amount_paise"] == 99900 and payment["status"] == "pending"


def test_legacy_sqlite_device_migration_adds_all_summary_columns_without_data_loss(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy-familyguard.sqlite3"
    with sqlite3.connect(db_path) as legacy:
        legacy.executescript("""
            CREATE TABLE users(uid TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created REAL NOT NULL);
            INSERT INTO users VALUES('legacy-user','legacy@example.com','hash',1.0);
            CREATE TABLE devices(
                device_id TEXT PRIMARY KEY,uid TEXT NOT NULL,device_name TEXT NOT NULL,
                platform TEXT NOT NULL,created REAL NOT NULL,last_seen REAL NOT NULL,
                battery INTEGER,lat REAL,lon REAL,accuracy REAL,captured REAL,
                location_updated_at REAL,
                FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE
            );
            INSERT INTO devices(device_id,uid,device_name,platform,created,last_seen)
            VALUES('legacy-device-123456','legacy-user','Legacy phone','android',1.0,2.0);
        """)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("FAMILYGUARD_DB_PATH", str(db_path))
    monkeypatch.delenv("RENDER_DISK_PATH", raising=False)
    import importlib
    import backend.main as main
    main = importlib.reload(main)
    main.init_db()
    expected = {
        "installed_apps", "screen_time_minutes", "today_calls_count",
        "today_calls_duration_sec", "contacts_count", "location_granted",
        "usage_granted", "calls_granted", "contacts_granted",
        "apps_sync_enabled", "calls_contacts_sync_enabled",
        "summary_last_synced_at", "sms_today_count", "sms_safety_status",
    }
    with main.db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
        row = conn.execute("SELECT device_name,uid FROM devices WHERE device_id='legacy-device-123456'").fetchone()
    assert expected.issubset(columns)
    assert row["device_name"] == "Legacy phone" and row["uid"] == "legacy-user"


def test_sos_requires_auth_owner_and_location_consent(api):
    _, client = api
    _, _, headers = register(client)
    _, _, stranger_headers = register(client)
    device_id = _registered_device(client, headers)
    payload={"message":"Need help","latitude":13.0827,"longitude":80.2707,"accuracyMeters":10,
             "capturedAt":datetime.now(timezone.utc).isoformat()}
    assert client.post(f"/api/devices/{device_id}/sos", json=payload).status_code == 401
    assert client.post(f"/api/devices/{device_id}/sos", headers=stranger_headers, json=payload).status_code == 404
    assert client.post(f"/api/devices/{device_id}/sos", headers=headers, json=payload).status_code == 403
    assert client.post("/api/privacy/consent", headers=headers, json={"deviceId":device_id,"location":True}).status_code == 200
    sent=client.post(f"/api/devices/{device_id}/sos",headers=headers,json=payload)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "active"
    alert_id=sent.json()["alertId"]
    assert client.get(f"/api/devices/{device_id}/sos").status_code == 401
    listed=client.get(f"/api/devices/{device_id}/sos",headers=headers)
    assert listed.status_code == 200 and listed.json()["alerts"][0]["status"] == "active"
    assert client.post(f"/api/devices/{device_id}/sos/{alert_id}/resolve",headers=stranger_headers).status_code == 404
    resolved=client.post(f"/api/devices/{device_id}/sos/{alert_id}/resolve",headers=headers)
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert client.get(f"/api/devices/{device_id}/sos",headers=headers).json()["alerts"][0]["status"] == "resolved"


def test_location_history_is_owner_scoped_and_consent_gated(api):
    _, client = api
    _, _, headers = register(client)
    _, _, stranger_headers = register(client)
    device_id=_registered_device(client,headers)
    assert client.get(f"/api/devices/{device_id}/locations",headers=headers).status_code == 403
    assert client.get(f"/api/devices/{device_id}/locations",headers=stranger_headers).status_code == 404
    family=client.post("/family/create",headers=headers,json={"displayName":"History"})
    assert family.status_code == 200
    family_id=family.json()["familyId"]
    assert client.post("/family/consent",headers=headers,json={"familyId":family_id,"shareLocation":True}).status_code == 200
    assert client.post("/api/privacy/consent",headers=headers,json={"deviceId":device_id,"location":True}).status_code == 200
    for lat in (13.0,13.1):
        update=client.post("/devices/location",headers=headers,json={
            "familyId":family_id,"deviceId":device_id,"latitude":lat,"longitude":80.0,
            "capturedAt":datetime.now(timezone.utc).isoformat()
        })
        assert update.status_code == 200, update.text
    history=client.get(f"/api/devices/{device_id}/locations",headers=headers)
    assert history.status_code == 200 and history.json()["count"] == 2
    assert len(history.json()["locations"]) == 2


def test_sms_summary_requires_explicit_consent_and_stores_aggregates_only(api):
    main, client = api
    _, user, headers = register(client)
    _, _, stranger_headers = register(client)
    device_id=_registered_device(client,headers)
    payload={"sms_today_count":6,"sms_safety_status":"review"}
    endpoint=f"/api/devices/{device_id}/sms-summary"
    assert client.post(endpoint,headers=headers,json=payload).status_code == 403
    assert client.post(endpoint,headers=stranger_headers,json=payload).status_code == 404
    consent=client.post("/api/privacy/consent",headers=headers,json={"deviceId":device_id,"sms_summary":True})
    assert consent.status_code == 200, consent.text
    saved=client.post(endpoint,headers=headers,json=payload)
    assert saved.status_code == 200, saved.text
    loaded=client.get(endpoint,headers=headers)
    assert loaded.status_code == 200 and loaded.json()["sms_today_count"] == 6
    with main.db() as conn:
        row=conn.execute("SELECT sms_today_count,sms_safety_status FROM devices WHERE device_id=? AND uid=?",(device_id,user["localId"])).fetchone()
    assert row["sms_today_count"] == 6 and row["sms_safety_status"] == "review"
    off=client.post("/api/privacy/consent",headers=headers,json={"deviceId":device_id,"sms_summary":False})
    assert off.status_code == 200
    assert client.get(endpoint,headers=headers).status_code == 403
    with main.db() as conn:
        row=conn.execute("SELECT sms_today_count,sms_safety_status FROM devices WHERE device_id=?",(device_id,)).fetchone()
    assert row["sms_today_count"] == 0 and row["sms_safety_status"] == "not_enabled"


def test_sos_rejects_stale_coordinates_and_invalid_safety_status(api):
    _, client = api
    _, _, headers = register(client)
    device_id=_registered_device(client,headers)
    assert client.post("/api/privacy/consent",headers=headers,json={"deviceId":device_id,"location":True}).status_code == 200
    stale={"message":"Help","latitude":13.0,"longitude":80.0,
           "capturedAt":"2020-01-01T00:00:00+00:00"}
    assert client.post(f"/api/devices/{device_id}/sos",headers=headers,json=stale).status_code == 400
    assert client.post(f"/api/devices/{device_id}/sms-summary",headers=headers,
                       json={"sms_today_count":2,"sms_safety_status":"unsafe"}).status_code == 422
