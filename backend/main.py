"""Self-contained FamilyGuard API using SQLite, scrypt passwords and signed JWTs.
Location sharing is opt-in, visible, time-limited and revocable.
"""
from __future__ import annotations
import os, sqlite3, hashlib, hmac, base64, json, secrets, time
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
    capturedAt: datetime
class VerifyRequest(BaseModel):
    packageName: str
    productId: str
    basePlanId: str
    purchaseToken: SecretStr=Field(min_length=10,max_length=4096)

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
        CREATE TABLE IF NOT EXISTS locations(family_id TEXT NOT NULL,uid TEXT NOT NULL,lat REAL NOT NULL,lon REAL NOT NULL,accuracy REAL,captured REAL NOT NULL,PRIMARY KEY(family_id,uid),FOREIGN KEY(family_id,uid) REFERENCES members(family_id,uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS entitlements(uid TEXT PRIMARY KEY,status TEXT NOT NULL,product_id TEXT,base_plan_id TEXT,token_hash TEXT,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS app_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
        """)
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
            c.execute("""INSERT INTO locations VALUES(?,?,?,?,?,?) ON CONFLICT(family_id,uid) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,accuracy=excluded.accuracy,captured=excluded.captured""",
            (row["family_id"],uid,payload.latitude,payload.longitude,payload.accuracyMeters,stamp))
    return {"updated":True,"familyCount":len(families),"capturedAt":captured.isoformat()}
@app.get("/location/family")
def location_family(familyId:str,uid:str=Depends(current_uid)):
    with db() as c:
        viewer=membership(c,uid,familyId)
        if not viewer["consent"]: raise HTTPException(403,"Enable your own location-sharing consent to view family locations")
        rows=c.execute("""SELECT l.uid,l.lat,l.lon,l.accuracy,l.captured FROM locations l JOIN members m ON m.family_id=l.family_id AND m.uid=l.uid WHERE l.family_id=? AND m.consent=1 AND l.captured>=?""",(familyId,time.time()-600)).fetchall()
    return {"familyId":familyId,"locations":[{"uid":r["uid"],"latitude":r["lat"],"longitude":r["lon"],"accuracyMeters":r["accuracy"],"capturedAt":datetime.fromtimestamp(r["captured"],timezone.utc).isoformat()} for r in rows]}
@app.delete("/account-data")
def delete_account_data(uid:str=Depends(current_uid)):
    with db() as c:
        c.execute("DELETE FROM users WHERE uid=?",(uid,))
    return {"deleted":True,"billingManagedBy":"Google Play","note":"Google Play subscription cancellation is separate."}
@app.get("/api")
def root(): return {"service":"FamilyGuard API","docs":"/docs","health":"/health"}


# Serve the installable browser dashboard from the same origin as the API.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="familyguard-web")
