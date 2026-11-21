"""FamilyGuard API: verified Play entitlements, Firebase-authenticated family pairing,
explicit consent and consent-gated live location sharing.

Location is stored in Firestore and protected by Firebase ID-token verification.
No location is collected by this server unless an authenticated app explicitly posts it.
"""
from __future__ import annotations

import base64
import json
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import firebase_admin
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from firebase_admin import auth as firebase_auth, firestore
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from pydantic import BaseModel, Field, SecretStr

APP_PACKAGE = os.getenv("PACKAGE_NAME", os.getenv("PLAY_PACKAGE_NAME", "com.princeofworld.familyguard"))
PRODUCT_ID = os.getenv("PLAY_SUBSCRIPTION_PRODUCT_ID", "familyguard_premium_999_monthly")
BASE_PLAN_ID = os.getenv("PLAY_SUBSCRIPTION_BASE_PLAN_ID", "monthly-999")
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64 = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64", "")
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
FIREBASE_SERVICE_ACCOUNT_JSON_BASE64 = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON_BASE64", "")
INVITE_TTL_MINUTES = int(os.getenv("INVITE_TTL_MINUTES", "15"))
MAX_BODY_BYTES = 16_384
SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]

app = FastAPI(title="FamilyGuard API", version="2.0.0")


class VerifyRequest(BaseModel):
    packageName: str = Field(min_length=3, max_length=255)
    productId: str = Field(min_length=3, max_length=255)
    basePlanId: str = Field(min_length=1, max_length=255)
    purchaseToken: SecretStr = Field(min_length=10, max_length=4096)


class FamilyCreate(BaseModel):
    displayName: str = Field(min_length=1, max_length=60)


class FamilyJoin(BaseModel):
    inviteCode: str = Field(pattern=r"^\d{6}$")


class ConsentRequest(BaseModel):
    familyId: str = Field(min_length=10, max_length=128)
    shareLocation: bool
    consentVersion: str = Field(default="location-v1", min_length=1, max_length=40)


class LocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracyMeters: float | None = Field(default=None, ge=0, le=100000)
    capturedAt: datetime


def _decode_service_account(raw: str, encoded: str) -> dict[str, Any] | None:
    try:
        if encoded:
            return json.loads(base64.b64decode(encoded, validate=True))
        if raw:
            return json.loads(raw)
    except Exception:
        raise HTTPException(status_code=503, detail="Firebase service credentials are invalid")
    return None


def _firebase_app():
    if firebase_admin._apps:
        return firebase_admin.get_app()
    info = _decode_service_account(FIREBASE_SERVICE_ACCOUNT_JSON, FIREBASE_SERVICE_ACCOUNT_JSON_BASE64)
    options = {}
    if info:
        return firebase_admin.initialize_app(firebase_admin.credentials.Certificate(info), options)
    # Render may alternatively provide GOOGLE_APPLICATION_CREDENTIALS.
    try:
        return firebase_admin.initialize_app()
    except Exception:
        raise HTTPException(status_code=503, detail="Firebase backend is not configured")


def _db():
    _firebase_app()
    return firestore.client()


def _uid(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Firebase ID token required")
    try:
        decoded = firebase_auth.verify_id_token(authorization[7:].strip(), check_revoked=True)
        return str(decoded["uid"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase ID token")


def _publisher_session() -> AuthorizedSession:
    info = _decode_service_account(GOOGLE_PLAY_SERVICE_ACCOUNT_JSON, GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64)
    if not info:
        raise HTTPException(status_code=503, detail="Google Play verification is not configured")
    try:
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return AuthorizedSession(creds)
    except Exception:
        raise HTTPException(status_code=503, detail="Google Play verification credentials are invalid")


def _active_premium(uid: str) -> bool:
    doc = _db().collection("familyguard_accounts").document(uid).get()
    if not doc.exists:
        return False
    data = doc.to_dict() or {}
    expiry = data.get("premiumExpiry")
    return bool(data.get("premiumActive")) and isinstance(expiry, datetime) and expiry > datetime.now(timezone.utc)


def _membership(uid: str, family_id: str) -> dict[str, Any]:
    db = _db()
    member = db.collection("families").document(family_id).collection("members").document(uid).get()
    if not member.exists:
        raise HTTPException(status_code=403, detail="You are not a member of this family")
    return member.to_dict() or {}


def _require_family_access(uid: str, family_id: str) -> dict[str, Any]:
    if not _active_premium(uid):
        return _membership(uid, family_id)
    return _membership(uid, family_id)


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and int(length) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request too large"})
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok", "service": "familyguard-api", "locationSharing": "consent-gated"}


@app.post("/verify-subscription")
def verify_subscription(payload: VerifyRequest, uid: str = Depends(_uid)):
    if payload.packageName != APP_PACKAGE or payload.productId != PRODUCT_ID or payload.basePlanId != BASE_PLAN_ID:
        raise HTTPException(status_code=400, detail="Product configuration mismatch")
    token = payload.purchaseToken.get_secret_value()
    url = ("https://androidpublisher.googleapis.com/androidpublisher/v3/"
           f"applications/{APP_PACKAGE}/purchases/subscriptionsv2/tokens/{token}")
    try:
        response = _publisher_session().get(url, timeout=12)
    except Exception:
        raise HTTPException(status_code=502, detail="Google Play verification temporarily unavailable")
    finally:
        token = ""
    if response.status_code == 404:
        raise HTTPException(status_code=403, detail="Purchase token was not recognized")
    if response.status_code in (401, 403):
        raise HTTPException(status_code=503, detail="Google Play API credentials lack permission")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Google Play verification failed")
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Invalid Google Play response")
    # Bind each purchase token to one Firebase account without storing the token itself.
    token_hash = hashlib.sha256(payload.purchaseToken.get_secret_value().encode("utf-8")).hexdigest()
    token_ref = _db().collection("familyguard_purchase_owners").document(token_hash)
    owner = token_ref.get()
    if owner.exists and (owner.to_dict() or {}).get("uid") != uid:
        raise HTTPException(status_code=409, detail="This purchase is already linked to another FamilyGuard account")
    matching = [x for x in data.get("lineItems", [])
                if x.get("productId") == PRODUCT_ID
                and (x.get("offerDetails") or {}).get("basePlanId") == BASE_PLAN_ID]
    now = datetime.now(timezone.utc)
    expiries = []
    for item in matching:
        raw = item.get("expiryTime")
        if raw:
            try:
                expiry = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if expiry > now:
                    expiries.append(expiry)
            except ValueError:
                pass
    active = data.get("subscriptionState") == "SUBSCRIPTION_STATE_ACTIVE" and bool(expiries)
    expiry = max(expiries) if expiries else None
    token_ref.set({"uid": uid, "updatedAt": firestore.SERVER_TIMESTAMP})
    _db().collection("familyguard_accounts").document(uid).set({
        "ownerUid": uid, "premiumActive": active, "premiumExpiry": expiry,
        "productId": PRODUCT_ID if active else None, "basePlanId": BASE_PLAN_ID if active else None,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return {"verified": True, "active": active, "productId": PRODUCT_ID if active else None,
            "basePlanId": BASE_PLAN_ID if active else None,
            "expiryTime": expiry.isoformat() if expiry else None}


@app.post("/family/create")
def family_create(payload: FamilyCreate, uid: str = Depends(_uid)):
    if not _active_premium(uid):
        raise HTTPException(status_code=402, detail="An active verified ₹999/month Premium subscription is required to create a family")
    db = _db()
    now = datetime.now(timezone.utc)
    family_ref = db.collection("families").document()
    code = f"{secrets.randbelow(1_000_000):06d}"
    invite_ref = db.collection("family_invites").document(code)
    # Retry collisions; codes expire quickly and can only be redeemed once.
    for _ in range(8):
        if not invite_ref.get().exists:
            break
        code = f"{secrets.randbelow(1_000_000):06d}"
        invite_ref = db.collection("family_invites").document(code)
    else:
        raise HTTPException(status_code=503, detail="Could not create an invite code; retry")
    batch = db.batch()
    batch.set(family_ref, {"name": payload.displayName, "ownerUid": uid, "createdAt": firestore.SERVER_TIMESTAMP})
    batch.set(family_ref.collection("members").document(uid), {
        "uid": uid, "role": "parent", "joinedAt": firestore.SERVER_TIMESTAMP,
        "locationConsent": False, "consentVersion": None,
    })
    batch.set(invite_ref, {"familyId": family_ref.id, "createdBy": uid,
                           "expiresAt": now + timedelta(minutes=INVITE_TTL_MINUTES),
                           "used": False, "createdAt": firestore.SERVER_TIMESTAMP})
    batch.commit()
    return {"familyId": family_ref.id, "inviteCode": code, "expiresInSeconds": INVITE_TTL_MINUTES * 60}


@app.post("/family/join")
def family_join(payload: FamilyJoin, uid: str = Depends(_uid)):
    db = _db()
    invite_ref = db.collection("family_invites").document(payload.inviteCode)
    @firestore.transactional
    def redeem(transaction):
        snap = invite_ref.get(transaction=transaction)
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Invite code not found")
        invite = snap.to_dict() or {}
        expiry = invite.get("expiresAt")
        if invite.get("used") or not isinstance(expiry, datetime) or expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Invite code expired or already used")
        family_id = invite["familyId"]
        member_ref = db.collection("families").document(family_id).collection("members").document(uid)
        transaction.set(member_ref, {"uid": uid, "role": "member", "joinedAt": firestore.SERVER_TIMESTAMP,
                                      "locationConsent": False, "consentVersion": None})
        transaction.update(invite_ref, {"used": True, "usedBy": uid, "usedAt": firestore.SERVER_TIMESTAMP})
        return family_id
    try:
        family_id = redeem(db.transaction())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=409, detail="Invite could not be redeemed")
    family = db.collection("families").document(family_id).get().to_dict() or {}
    return {"familyId": family_id, "familyName": family.get("name", "Family"), "joined": True}


@app.post("/family/consent")
def family_consent(payload: ConsentRequest, uid: str = Depends(_uid)):
    db = _db()
    _membership(uid, payload.familyId)
    member_ref = db.collection("families").document(payload.familyId).collection("members").document(uid)
    member_ref.set({"locationConsent": payload.shareLocation,
                    "consentVersion": payload.consentVersion,
                    "consentUpdatedAt": firestore.SERVER_TIMESTAMP}, merge=True)
    # Revoking consent immediately removes this member's last stored location.
    if not payload.shareLocation:
        db.collection("families").document(payload.familyId).collection("locations").document(uid).delete()
    return {"familyId": payload.familyId, "locationConsent": payload.shareLocation}


@app.post("/location/update")
def location_update(payload: LocationUpdate, uid: str = Depends(_uid)):
    db = _db()
    memberships = list(db.collection_group("members").where("uid", "==", uid).limit(10).stream())
    active_families = []
    for member in memberships:
        m = member.to_dict() or {}
        if m.get("locationConsent") is True:
            family_id = member.reference.parent.parent.id
            active_families.append(family_id)
    if not active_families:
        raise HTTPException(status_code=403, detail="Join a family and explicitly enable location consent first")
    now = datetime.now(timezone.utc)
    captured = payload.capturedAt
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    if abs((now - captured).total_seconds()) > 300:
        raise HTTPException(status_code=400, detail="Location timestamp must be within five minutes")
    if len(active_families) > 5:
        raise HTTPException(status_code=409, detail="Too many family memberships")
    batch = db.batch()
    for family_id in active_families:
        _membership(uid, family_id)
        batch.set(db.collection("families").document(family_id).collection("locations").document(uid), {
            "uid": uid, "latitude": payload.latitude, "longitude": payload.longitude,
            "accuracyMeters": payload.accuracyMeters, "capturedAt": captured,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        })
    batch.commit()
    return {"updated": True, "familyCount": len(active_families), "capturedAt": captured.isoformat()}


@app.get("/location/family")
def location_family(familyId: str, uid: str = Depends(_uid)):
    db = _db()
    _membership(uid, familyId)
    viewer = db.collection("families").document(familyId).collection("members").document(uid).get().to_dict() or {}
    if viewer.get("locationConsent") is not True:
        raise HTTPException(status_code=403, detail="Enable your own location-sharing consent to view family locations")
    members = list(db.collection("families").document(familyId).collection("members").stream())
    permitted = {m.id for m in members if (m.to_dict() or {}).get("locationConsent") is True}
    locations = []
    for doc in db.collection("families").document(familyId).collection("locations").stream():
        if doc.id not in permitted:
            continue
        item = doc.to_dict() or {}
        captured = item.get("capturedAt")
        if not isinstance(captured, datetime) or captured < datetime.now(timezone.utc) - timedelta(minutes=10):
            continue
        locations.append({"uid": doc.id, "latitude": item.get("latitude"),
                          "longitude": item.get("longitude"), "accuracyMeters": item.get("accuracyMeters"),
                          "capturedAt": captured.isoformat() if isinstance(captured, datetime) else None})
    return {"familyId": familyId, "locations": locations}


@app.delete("/account-data")
def delete_account_data(uid: str = Depends(_uid)):
    db = _db()
    # Remove this member's locations, membership records, and account record.
    memberships = list(db.collection_group("members").where("uid", "==", uid).stream())
    batch = db.batch()
    for member in memberships:
        family_ref = member.reference.parent.parent
        batch.delete(member.reference)
        batch.delete(family_ref.collection("locations").document(uid))
    batch.delete(db.collection("familyguard_accounts").document(uid))
    batch.commit()
    return {"deleted": True, "deletedFamilyMemberships": len(memberships),
            "billingManagedBy": "Google Play"}


@app.get("/")
def root():
    return {"service": "FamilyGuard API", "docs": "/docs", "health": "/health"}
