"""FamilyGuard server-side Google Play subscription verification API.

Secrets are supplied only through environment variables. Purchase tokens are never
logged or persisted. The service returns an entitlement only after Google Play
Developer API reports an active, unexpired subscription for the configured app.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import firebase_admin
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from firebase_admin import auth as firebase_auth, firestore
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from pydantic import BaseModel, Field, SecretStr

APP_PACKAGE = os.getenv("PLAY_PACKAGE_NAME", "com.princeofworld.familyguard")
PRODUCT_ID = os.getenv("PLAY_SUBSCRIPTION_PRODUCT_ID", "familyguard_premium_999_monthly")
BASE_PLAN_ID = os.getenv("PLAY_SUBSCRIPTION_BASE_PLAN_ID", "monthly-999")
SERVICE_ACCOUNT_B64 = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64", "")
MAX_BODY_BYTES = 16_384
SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]

app = FastAPI(title="FamilyGuard Verification API", version="1.0.0")


class VerifyRequest(BaseModel):
    packageName: str = Field(min_length=3, max_length=255)
    productId: str = Field(min_length=3, max_length=255)
    basePlanId: str = Field(min_length=1, max_length=255)
    purchaseToken: SecretStr = Field(min_length=10, max_length=4096)


def _publisher_session() -> AuthorizedSession:
    if not SERVICE_ACCOUNT_B64:
        raise HTTPException(status_code=503, detail="Google Play verification is not configured")
    try:
        raw = base64.b64decode(SERVICE_ACCOUNT_B64, validate=True)
        info = json.loads(raw)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return AuthorizedSession(credentials)
    except Exception:
        # Do not leak credential parsing details to callers or logs.
        raise HTTPException(status_code=503, detail="Google Play verification is not configured")


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and int(length) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request too large"})
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "familyguard-verification"}


@app.post("/verify-subscription")
def verify_subscription(payload: VerifyRequest, request: Request) -> dict[str, Any]:
    if payload.packageName != APP_PACKAGE or payload.productId != PRODUCT_ID or payload.basePlanId != BASE_PLAN_ID:
        raise HTTPException(status_code=400, detail="Product configuration mismatch")

    token = payload.purchaseToken.get_secret_value()
    url = (
        "https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{APP_PACKAGE}/purchases/subscriptionsv2/tokens/{token}"
    )
    try:
        response = _publisher_session().get(url, timeout=12)
    except Exception:
        raise HTTPException(status_code=502, detail="Google Play verification temporarily unavailable")
    finally:
        token = ""  # avoid retaining the token in local scope longer than necessary

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

    state = data.get("subscriptionState")
    if state != "SUBSCRIPTION_STATE_ACTIVE":
        return {"verified": True, "active": False, "state": state or "UNKNOWN"}

    matching = []
    for item in data.get("lineItems", []):
        offer = item.get("offerDetails") or {}
        if item.get("productId") == PRODUCT_ID and offer.get("basePlanId") == BASE_PLAN_ID:
            matching.append(item)

    now = datetime.now(timezone.utc)
    active_until = None
    for item in matching:
        raw_expiry = item.get("expiryTime")
        if raw_expiry:
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
                if expiry > now and (active_until is None or expiry > active_until):
                    active_until = expiry
            except ValueError:
                continue

    if active_until is None:
        return {"verified": True, "active": False, "state": state}

    return {
        "verified": True,
        "active": True,
        "productId": PRODUCT_ID,
        "basePlanId": BASE_PLAN_ID,
        "expiryTime": active_until.isoformat(),
    }


def _verified_firebase_uid(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Firebase ID token required")
    token = authorization[7:].strip()
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        decoded = firebase_auth.verify_id_token(token, check_revoked=True)
        return str(decoded["uid"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired account token")


@app.delete("/account-data")
def delete_account_data(uid: str = Depends(_verified_firebase_uid)) -> dict[str, Any]:
    """Delete FamilyGuard-owned Firestore data for the authenticated Firebase user.

    Google Play purchase records are intentionally not deleted here. Subscription
    cancellation is managed separately by the user in Google Play.
    """
    try:
        db = firestore.client()
        batch = db.batch()
        docs = list(db.collection("familyguard_user_data").where("ownerUid", "==", uid).stream())
        for doc in docs:
            batch.delete(doc.reference)
        batch.delete(db.collection("familyguard_accounts").document(uid))
        batch.commit()
        return {"deleted": True, "deletedDocuments": len(docs), "billingManagedBy": "Google Play"}
    except Exception:
        raise HTTPException(status_code=503, detail="Account data deletion is temporarily unavailable")
