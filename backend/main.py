"""Self-contained FamilyGuard API using SQLite, scrypt passwords and signed JWTs.
Location sharing is opt-in, visible, time-limited and revocable.
"""
from __future__ import annotations
import os, sqlite3, hashlib, hmac, base64, json, secrets, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, EmailStr

APP_PACKAGE=os.getenv("PACKAGE_NAME","com.princeofworld.familyguard")
PRODUCT_ID=os.getenv("PLAY_SUBSCRIPTION_PRODUCT_ID","familyguard_premium_999_monthly")
BASE_PLAN_ID=os.getenv("PLAY_SUBSCRIPTION_BASE_PLAN_ID","monthly-999")
INVITE_TTL_MINUTES=max(5,min(60,int(os.getenv("INVITE_TTL_MINUTES","15"))))
MAX_BODY_BYTES=16384
DB_PATH=os.getenv("FAMILYGUARD_DB_PATH", str(Path(os.getenv("RENDER_DISK_PATH","/var/data"))/"familyguard.sqlite3"))
if not os.getenv("RENDER_DISK_PATH") and not Path("/var/data").exists():
    DB_PATH=os.getenv("FAMILYGUARD_DB_PATH", str(Path(__file__).resolve().parent/"familyguard.sqlite3"))
Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
app=FastAPI(title="FamilyGuard API",version="3.0.0")

class AuthIn(BaseModel):
    email: EmailStr
    password: SecretStr=Field(min_length=10,max_length=128)
class FamilyCreate(BaseModel):
    displayName: str=Field(min_length=1,max_length=60)
class FamilyJoin(BaseModel):
    inviteCode: str=Field(pattern=r"^\d{6}$")
class ConsentRequest(BaseModel):
    familyId: str=Field(min_length=12,max_length=64)
    shareLocation: bool
    consentVersion: str=Field(default="location-v1",max_length=40)
class LocationUpdate(BaseModel):
    latitude: float=Field(ge=-90,le=90)
    longitude: float=Field(ge=-180,le=180)
    accuracyMeters: Optional[float]=Field(default=None,ge=0,le=100000)
    batteryPercent: Optional[int]=Field(default=None,ge=0,le=100)
    capturedAt: datetime
class VerifyRequest(BaseModel):
    packageName: str
    productId: str
    basePlanId: str
    purchaseToken: SecretStr=Field(min_length=10,max_length=4096)
class SubscriptionVerify(BaseModel):
    razorpay_subscription_id: str=Field(min_length=8,max_length=80)
    razorpay_payment_id: str=Field(min_length=8,max_length=80)
    razorpay_signature: str=Field(min_length=32,max_length=128)

def db():
    c=sqlite3.connect(DB_PATH,timeout=15); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA journal_mode=WAL")
    return c
def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS families(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner_uid TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS members(family_id TEXT NOT NULL,uid TEXT NOT NULL,role TEXT NOT NULL,consent INTEGER NOT NULL DEFAULT 0,consent_version TEXT,consent_at REAL,PRIMARY KEY(family_id,uid),FOREIGN KEY(family_id) REFERENCES families(id) ON DELETE CASCADE,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,family_id TEXT NOT NULL,created_by TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS locations(family_id TEXT NOT NULL,uid TEXT NOT NULL,lat REAL NOT NULL,lon REAL NOT NULL,accuracy REAL,captured REAL NOT NULL,battery INTEGER,PRIMARY KEY(family_id,uid),FOREIGN KEY(family_id,uid) REFERENCES members(family_id,uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS entitlements(uid TEXT PRIMARY KEY,status TEXT NOT NULL,product_id TEXT,base_plan_id TEXT,token_hash TEXT,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS app_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_subscriptions(subscription_id TEXT PRIMARY KEY,uid TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        """)
        columns={r["name"] for r in c.execute("PRAGMA table_info(locations)").fetchall()}
        if "battery" not in columns: c.execute("ALTER TABLE locations ADD COLUMN battery INTEGER")
        row=c.execute("SELECT v FROM app_meta WHERE k='jwt_secret'").fetchone()
        if not row: c.execute("INSERT INTO app_meta VALUES('jwt_secret',?)",(secrets.token_urlsafe(48),))
init_db()
def secret():
    with db() as c: return c.execute("SELECT v FROM app_meta WHERE k='jwt_secret'").fetchone()["v"].encode()
def b64(x:bytes)->str: return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
def jwt_make(uid:str)->str:
    head=b64(b'{"alg":"HS256","typ":"JWT"}')
    payload=b64(json.dumps({"sub":uid,"iat":int(time.time()),"exp":int(time.time())+43200}).encode())
    msg=f"{head}.{payload}".encode()
    return f"{head}.{payload}.{b64(hmac.new(secret(),msg,hashlib.sha256).digest())}"
def current_uid(authorization: str|None=Header(default=None))->str:
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Bearer access token required")
    try:
        h,p,s=authorization[7:].split("."); msg=f"{h}.{p}".encode()
        if not hmac.compare_digest(b64(hmac.new(secret(),msg,hashlib.sha256).digest()),s): raise ValueError()
        claims=json.loads(base64.urlsafe_b64decode(p+"="*((4-len(p)%4)%4)))
        if int(claims["exp"])<time.time(): raise ValueError()
        uid=claims["sub"]
        with db() as c:
            if not c.execute("SELECT 1 FROM users WHERE uid=?",(uid,)).fetchone(): raise ValueError()
        return uid
    except Exception: raise HTTPException(401,"Invalid or expired access token")
def hash_password(password:str,salt:bytes|None=None)->str:
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)
    return b64(salt)+":"+b64(digest)
def check_password(password:str,stored:str)->bool:
    try:
        a,b=stored.split(":"); salt=base64.urlsafe_b64decode(a+"="*((4-len(a)%4)%4))
        return hmac.compare_digest(hash_password(password,salt).split(":")[1],b)
    except Exception: return False
def membership(c,uid,fid):
    row=c.execute("SELECT * FROM members WHERE uid=? AND family_id=?",(uid,fid)).fetchone()
    if not row: raise HTTPException(403,"You are not a member of this family")
    return row
def premium_active(c,uid):
    row=c.execute("SELECT status FROM entitlements WHERE uid=?",(uid,)).fetchone()
    return bool(row and row["status"]=="verified_active")

@app.middleware("http")
async def size_limit(request:Request,call_next):
    length=request.headers.get("content-length")
    if length and int(length)>MAX_BODY_BYTES: return JSONResponse(status_code=413,content={"detail":"Request too large"})
    return await call_next(request)
@app.get("/health")
def health(): return {"status":"ok","service":"familyguard-api","auth":"sqlite-jwt","storage":"sqlite","locationSharing":"consent-gated"}
@app.post("/auth/register")
def register(payload:AuthIn):
    email=str(payload.email).lower().strip()
    if len(payload.password.get_secret_value())<10: raise HTTPException(400,"Password must contain at least 10 characters")
    uid=secrets.token_urlsafe(18)
    try:
        with db() as c: c.execute("INSERT INTO users VALUES(?,?,?,?)",(uid,email,hash_password(payload.password.get_secret_value()),time.time()))
    except sqlite3.IntegrityError: raise HTTPException(409,"An account with this email already exists")
    token=jwt_make(uid)
    return {"idToken":token,"refreshToken":"","localId":uid,"expiresIn":"43200","authProvider":"FamilyGuard"}
@app.post("/auth/login")
def login(payload:AuthIn):
    with db() as c: row=c.execute("SELECT * FROM users WHERE email=?",(str(payload.email).lower().strip(),)).fetchone()
    if not row or not check_password(payload.password.get_secret_value(),row["password_hash"]): raise HTTPException(401,"Email or password is incorrect")
    return {"idToken":jwt_make(row["uid"]),"refreshToken":"","localId":row["uid"],"expiresIn":"43200","authProvider":"FamilyGuard"}
@app.post("/verify-subscription")
def verify_subscription(payload:VerifyRequest,uid:str=Depends(current_uid)):
    if payload.packageName!=APP_PACKAGE or payload.productId!=PRODUCT_ID or payload.basePlanId!=BASE_PLAN_ID: raise HTTPException(400,"Product configuration mismatch")
    # Without a Play Developer service account, a purchase token cannot be trusted as proof of payment.
    token_hash=hashlib.sha256(payload.purchaseToken.get_secret_value().encode()).hexdigest()
    with db() as c:
        existing=c.execute("SELECT uid FROM entitlements WHERE token_hash=?",(token_hash,)).fetchone()
        if existing and existing["uid"]!=uid: raise HTTPException(409,"Purchase token is already linked to another account")
        c.execute("""INSERT INTO entitlements(uid,status,product_id,base_plan_id,token_hash,updated) VALUES(?,?,?,?,?,?)
        ON CONFLICT(uid) DO UPDATE SET status='pending_verification',product_id=excluded.product_id,base_plan_id=excluded.base_plan_id,token_hash=excluded.token_hash,updated=excluded.updated""",
        (uid,"pending_verification",PRODUCT_ID,BASE_PLAN_ID,token_hash,time.time()))
    return {"verified":False,"active":False,"status":"pending_verification","message":"Purchase token recorded securely. Google Play Developer API credentials are required to verify payment; Premium family creation remains locked."}
@app.post("/family/create")
def family_create(payload:FamilyCreate,uid:str=Depends(current_uid)):
    with db() as c:
        if not premium_active(c,uid): raise HTTPException(402,"Active verified Premium subscription is required. Purchase is pending until Google Play verification is configured.")
        fid=secrets.token_urlsafe(18); now=time.time()
        c.execute("INSERT INTO families VALUES(?,?,?,?)",(fid,payload.displayName.strip(),uid,now))
        c.execute("INSERT INTO members(family_id,uid,role,consent) VALUES(?,?,?,0)",(fid,uid,"parent"))
        code=None
        for _ in range(20):
            candidate=f"{secrets.randbelow(1000000):06d}"
            if not c.execute("SELECT 1 FROM invites WHERE code=?",(candidate,)).fetchone(): code=candidate; break
        if code is None: raise HTTPException(503,"Could not allocate invite code")
        c.execute("INSERT INTO invites VALUES(?,?,?, ?,0)",(code,fid,uid,now+INVITE_TTL_MINUTES*60))
    return {"familyId":fid,"inviteCode":code,"expiresInSeconds":INVITE_TTL_MINUTES*60}
@app.post("/family/join")
def family_join(payload:FamilyJoin,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        inv=c.execute("SELECT * FROM invites WHERE code=?",(payload.inviteCode,)).fetchone()
        if not inv: raise HTTPException(404,"Invite code not found")
        if inv["used"] or inv["expires"]<=now: raise HTTPException(410,"Invite code expired or already used")
        c.execute("INSERT OR IGNORE INTO members(family_id,uid,role,consent) VALUES(?,?,?,0)",(inv["family_id"],uid,"member"))
        c.execute("UPDATE invites SET used=1 WHERE code=? AND used=0",(payload.inviteCode,))
        fam=c.execute("SELECT name FROM families WHERE id=?",(inv["family_id"],)).fetchone()
    return {"familyId":inv["family_id"],"familyName":fam["name"] if fam else "Family","joined":True}
@app.post("/family/consent")
def family_consent(payload:ConsentRequest,uid:str=Depends(current_uid)):
    with db() as c:
        membership(c,uid,payload.familyId)
        c.execute("UPDATE members SET consent=?,consent_version=?,consent_at=? WHERE family_id=? AND uid=?",(1 if payload.shareLocation else 0,payload.consentVersion,time.time(),payload.familyId,uid))
        if not payload.shareLocation: c.execute("DELETE FROM locations WHERE family_id=? AND uid=?",(payload.familyId,uid))
    return {"familyId":payload.familyId,"locationConsent":payload.shareLocation}
@app.post("/location/update")
def location_update(payload:LocationUpdate,uid:str=Depends(current_uid)):
    captured=payload.capturedAt
    if captured.tzinfo is None: captured=captured.replace(tzinfo=timezone.utc)
    stamp=captured.timestamp(); now=time.time()
    if abs(now-stamp)>300: raise HTTPException(400,"Location timestamp must be within five minutes")
    with db() as c:
        families=c.execute("SELECT family_id FROM members WHERE uid=? AND consent=1",(uid,)).fetchall()
        if not families: raise HTTPException(403,"Join a family and explicitly enable location consent first")
        for row in families:
            c.execute("""INSERT INTO locations(family_id,uid,lat,lon,accuracy,captured,battery) VALUES(?,?,?,?,?,?,?) ON CONFLICT(family_id,uid) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,accuracy=excluded.accuracy,captured=excluded.captured,battery=excluded.battery""",
            (row["family_id"],uid,payload.latitude,payload.longitude,payload.accuracyMeters,stamp,payload.batteryPercent))
    return {"updated":True,"familyCount":len(families),"capturedAt":captured.isoformat()}
@app.get("/location/family")
def location_family(familyId:str,uid:str=Depends(current_uid)):
    with db() as c:
        membership(c,uid,familyId)
        # A family member may view only locations shared by members who individually consented.
        rows=c.execute("""SELECT l.uid,l.lat,l.lon,l.accuracy,l.captured,l.battery FROM locations l JOIN members m ON m.family_id=l.family_id AND m.uid=l.uid WHERE l.family_id=? AND m.consent=1 AND l.captured>=?""",(familyId,time.time()-600)).fetchall()
    return {"familyId":familyId,"locations":[{"uid":r["uid"],"latitude":r["lat"],"longitude":r["lon"],"accuracyMeters":r["accuracy"],"batteryPercent":r["battery"],"isOnline":(time.time()-r["captured"])<=180,"capturedAt":datetime.fromtimestamp(r["captured"],timezone.utc).isoformat()} for r in rows]}

def razorpay_configured():
    return bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET") and os.getenv("RAZORPAY_PLAN_ID"))
def razorpay_request(path: str, method: str="GET", payload: dict|None=None):
    key=os.getenv("RAZORPAY_KEY_ID","").strip()
    secret_value=os.getenv("RAZORPAY_KEY_SECRET","").strip()
    if not key or not secret_value: raise HTTPException(503,"Razorpay is not configured. Premium checkout is temporarily unavailable.")
    raw=json.dumps(payload or {}).encode() if payload is not None else None
    auth=base64.b64encode(f"{key}:{secret_value}".encode()).decode()
    req=urllib.request.Request("https://api.razorpay.com/v1/"+path.lstrip("/"),data=raw,method=method,headers={"Authorization":"Basic "+auth,"Content-Type":"application/json","Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=15) as response: return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode(errors="replace")[:500]
        raise HTTPException(502,"Razorpay request failed. Check the merchant plan and API configuration.") from exc
    except Exception as exc:
        raise HTTPException(502,"Razorpay could not be reached. Please retry later.") from exc

@app.get("/billing/config")
def billing_config():
    return {"provider":"razorpay","configured":razorpay_configured(),"amount":99900,"currency":"INR","interval":"monthly","displayPrice":"₹999/month","keyId":os.getenv("RAZORPAY_KEY_ID") if razorpay_configured() else None}

@app.get("/billing/status")
def billing_status(uid:str=Depends(current_uid)):
    with db() as c:
        row=c.execute("SELECT status,updated FROM entitlements WHERE uid=?",(uid,)).fetchone()
        sub=c.execute("SELECT subscription_id,status,updated FROM payment_subscriptions WHERE uid=? ORDER BY updated DESC LIMIT 1",(uid,)).fetchone()
    active=bool(row and row["status"]=="verified_active")
    return {"plan":"premium" if active else "free","premium":active,"status":row["status"] if row else "free","subscriptionStatus":sub["status"] if sub else None,"updatedAt":row["updated"] if row else None}

@app.post("/payment/create-order")
def payment_create_order(uid:str=Depends(current_uid)):
    if not razorpay_configured():
        return JSONResponse(status_code=503,content={"configured":False,"detail":"Razorpay credentials and a monthly ₹999 plan are not configured. No payment has been taken."})
    with db() as c:
        user=c.execute("SELECT email FROM users WHERE uid=?",(uid,)).fetchone()
    plan_id=os.getenv("RAZORPAY_PLAN_ID","").strip()
    payload={"plan_id":plan_id,"total_count":int(os.getenv("RAZORPAY_SUBSCRIPTION_CYCLES","120")),"quantity":1,"customer_notify":True,"notes":{"familyguard_uid":uid,"product":"FamilyGuard Premium ₹999 monthly"}}
    created=razorpay_request("subscriptions","POST",payload)
    sub_id=created.get("id")
    if not sub_id: raise HTTPException(502,"Razorpay did not return a subscription ID.")
    now=time.time()
    with db() as c:
        c.execute("INSERT OR REPLACE INTO payment_subscriptions(subscription_id,uid,status,created,updated) VALUES(?,?,?,?,?)",(sub_id,uid,created.get("status","created"),now,now))
    return {"configured":True,"provider":"razorpay","keyId":os.getenv("RAZORPAY_KEY_ID"),"subscriptionId":sub_id,"shortUrl":created.get("short_url"),"amount":99900,"currency":"INR","name":"FamilyGuard Premium","description":"₹999 per month recurring subscription","prefill":{"email":user["email"] if user else ""}}

@app.post("/payment/verify")
def payment_verify(payload:SubscriptionVerify,uid:str=Depends(current_uid)):
    secret_value=os.getenv("RAZORPAY_KEY_SECRET","").strip()
    if not secret_value: raise HTTPException(503,"Razorpay verification is not configured. Premium remains locked.")
    with db() as c:
        saved=c.execute("SELECT * FROM payment_subscriptions WHERE subscription_id=? AND uid=?",(payload.razorpay_subscription_id,uid)).fetchone()
    if not saved: raise HTTPException(403,"Subscription does not belong to this account.")
    expected=hmac.new(secret_value.encode(),f"{payload.razorpay_payment_id}|{payload.razorpay_subscription_id}".encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,payload.razorpay_signature): raise HTTPException(400,"Razorpay signature verification failed.")
    remote=razorpay_request("subscriptions/"+payload.razorpay_subscription_id)
    status=remote.get("status","unknown")
    if remote.get("plan_id")!=os.getenv("RAZORPAY_PLAN_ID"): raise HTTPException(400,"Subscription plan does not match FamilyGuard Premium.")
    notes=remote.get("notes") or {}
    if notes.get("familyguard_uid")!=uid: raise HTTPException(403,"Subscription account verification failed.")
    active=status in ("active","authenticated") and int(remote.get("paid_count") or 0)>=1
    now=time.time()
    with db() as c:
        c.execute("UPDATE payment_subscriptions SET status=?,updated=? WHERE subscription_id=?",(status,now,payload.razorpay_subscription_id))
        if active:
            c.execute("""INSERT INTO entitlements(uid,status,product_id,base_plan_id,token_hash,updated) VALUES(?,?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET status='verified_active',product_id=excluded.product_id,base_plan_id=excluded.base_plan_id,token_hash=excluded.token_hash,updated=excluded.updated""",
            (uid,"verified_active","familyguard_razorpay_monthly_999","monthly-999",payload.razorpay_subscription_id,now))
    return {"verified":True,"active":active,"status":status,"message":"Premium activated after Razorpay signature and subscription checks." if active else "Payment callback verified; subscription activation is still pending. Premium remains locked until Razorpay confirms the first paid cycle."}

@app.post("/payment/webhook")
async def payment_webhook(request:Request):
    secret_value=os.getenv("RAZORPAY_WEBHOOK_SECRET","").strip()
    if not secret_value: raise HTTPException(503,"Webhook verification is not configured.")
    raw=await request.body()
    supplied=request.headers.get("X-Razorpay-Signature","")
    expected=hmac.new(secret_value.encode(),raw,hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(expected,supplied): raise HTTPException(400,"Invalid Razorpay webhook signature.")
    event=json.loads(raw.decode())
    event_name=event.get("event","")
    entity=((event.get("payload") or {}).get("subscription") or {}).get("entity") or {}
    sub_id=entity.get("id")
    if not sub_id: return {"received":True,"ignored":True}
    states={"subscription.activated":"verified_active","subscription.charged":"verified_active","subscription.authenticated":"verified_active","subscription.cancelled":"cancelled","subscription.halted":"halted","subscription.paused":"paused","subscription.completed":"completed"}
    state=states.get(event_name)
    if not state: return {"received":True,"ignored":True}
    now=time.time()
    with db() as c:
        saved=c.execute("SELECT uid FROM payment_subscriptions WHERE subscription_id=?",(sub_id,)).fetchone()
        if not saved:
            uid=(entity.get("notes") or {}).get("familyguard_uid")
            if uid and c.execute("SELECT 1 FROM users WHERE uid=?",(uid,)).fetchone():
                c.execute("INSERT INTO payment_subscriptions(subscription_id,uid,status,created,updated) VALUES(?,?,?,?,?)",(sub_id,uid,state,now,now))
                saved={"uid":uid}
        if saved:
            uid=saved["uid"]
            c.execute("UPDATE payment_subscriptions SET status=?,updated=? WHERE subscription_id=?",(state,now,sub_id))
            if state=="verified_active":
                c.execute("""INSERT INTO entitlements(uid,status,product_id,base_plan_id,token_hash,updated) VALUES(?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET status='verified_active',product_id=excluded.product_id,base_plan_id=excluded.base_plan_id,token_hash=excluded.token_hash,updated=excluded.updated""",
                (uid,"verified_active","familyguard_razorpay_monthly_999","monthly-999",sub_id,now))
            else:
                c.execute("UPDATE entitlements SET status='inactive',updated=? WHERE uid=? AND token_hash=?",(now,uid,sub_id))
    return {"received":True}

@app.get("/app/latest-version")
def latest_app_version():
    return {"versionName":os.getenv("APP_VERSION_NAME","1.3.1"),"versionCode":int(os.getenv("APP_VERSION_CODE","6")),"minimumVersionCode":int(os.getenv("APP_MINIMUM_VERSION_CODE","5")),"apkUrl":os.getenv("APP_APK_URL","https://github.com/princeofworld/PrinceHacker/releases/latest/download/FamilyGuard-release-signed.apk"),"releaseNotes":"Official FamilyGuard update. Review the changes before installing.","forceUpdate":False}

@app.delete("/account-data")
def delete_account_data(uid:str=Depends(current_uid)):
    with db() as c:
        c.execute("DELETE FROM users WHERE uid=?",(uid,))
    return {"deleted":True,"billingManagedBy":"Razorpay (if configured)","note":"Cancel recurring payments through the FamilyGuard support channel or Razorpay mandate controls."}
@app.get("/api")
def root(): return {"service":"FamilyGuard API","docs":"/docs","health":"/health"}


# Serve the installable browser dashboard from the same origin as the API.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="familyguard-web")
