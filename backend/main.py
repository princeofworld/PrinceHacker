"""Self-contained FamilyGuard API using SQLite, scrypt passwords and signed JWTs.
Location sharing is opt-in, visible, time-limited and revocable.
"""
from __future__ import annotations
import os, sqlite3, hashlib, hmac, base64, json, secrets, time, urllib.request, urllib.error, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, EmailStr

DATABASE_URL=os.getenv("DATABASE_URL","").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL="postgresql://"+DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://") and "sslmode=" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?")+"sslmode=require"
DB_KIND="postgresql" if DATABASE_URL.startswith("postgresql://") else "sqlite"
if DB_KIND=="postgresql":
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.errors import IntegrityError as PostgresIntegrityError
        DB_INTEGRITY_ERRORS=(sqlite3.IntegrityError,PostgresIntegrityError)
    except ImportError as exc:
        raise RuntimeError("DATABASE_URL is set but psycopg is missing. Install backend/requirements.txt.") from exc
else:
    DB_INTEGRITY_ERRORS=(sqlite3.IntegrityError,)

INVITE_TTL_MINUTES=max(5,min(60,int(os.getenv("INVITE_TTL_MINUTES","15"))))
MAX_BODY_BYTES=16384
DB_PATH=os.getenv("FAMILYGUARD_DB_PATH", str(Path(os.getenv("RENDER_DISK_PATH","/var/data"))/"familyguard.sqlite3"))
if not os.getenv("RENDER_DISK_PATH") and not Path("/var/data").exists():
    DB_PATH=os.getenv("FAMILYGUARD_DB_PATH", str(Path(__file__).resolve().parent/"familyguard.sqlite3"))
if DB_KIND=="sqlite":
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
app=FastAPI(title="FamilyGuard API",version="3.0.0")

class RefreshIn(BaseModel):
    refreshToken: str=Field(min_length=20,max_length=256)
class SubscriptionVerify(BaseModel):
    razorpay_payment_id: str=Field(min_length=1,max_length=128)
    razorpay_subscription_id: str=Field(min_length=1,max_length=128)
    razorpay_signature: str=Field(min_length=1,max_length=256)

class AuthIn(BaseModel):
    email: EmailStr
    password: SecretStr=Field(min_length=10,max_length=128)
class PasswordChangeIn(BaseModel):
    currentPassword: SecretStr=Field(min_length=1,max_length=128)
    newPassword: SecretStr=Field(min_length=10,max_length=128)
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
    familyId: str=Field(min_length=12,max_length=64)
    deviceId: Optional[str]=Field(default=None,min_length=16,max_length=80)

class DeviceRegister(BaseModel):
    deviceId: str=Field(min_length=16,max_length=80)
    deviceName: str=Field(min_length=1,max_length=100)
    platform: str=Field(default="android",min_length=1,max_length=30)
    batteryPercent: Optional[int]=Field(default=None,ge=0,le=100)
class DatabaseConnection:
    """Small DB-API compatibility layer for the existing SQLite-style queries."""
    def __init__(self):
        self.is_postgres=(DB_KIND=="postgresql")
        if self.is_postgres:
            self.connection=psycopg.connect(DATABASE_URL,connect_timeout=10,row_factory=dict_row)
        else:
            self.connection=sqlite3.connect(DB_PATH,timeout=15)
            self.connection.row_factory=sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
    def __enter__(self): return self
    def __exit__(self,exc_type,exc,tb):
        try:
            self.connection.rollback() if exc_type else self.connection.commit()
        finally:
            self.connection.close()
        return False
    def execute(self,sql,params=()):
        if self.is_postgres:
            if sql.lstrip().upper().startswith("PRAGMA"):
                pragma=re.match(r"\s*PRAGMA\s+table_info\(([^)]+)\)",sql,re.I)
                if pragma:
                    return self.connection.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s",(pragma.group(1).strip(),))
                return self.connection.execute("SELECT 1 WHERE FALSE")
            is_ignore=bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",sql,re.I))
            is_replace=bool(re.search(r"\bINSERT\s+OR\s+REPLACE\s+INTO\s+payment_subscriptions\b",sql,re.I))
            sql=re.sub(r"\bINSERT\s+OR\s+(?:IGNORE|REPLACE)\s+INTO\b","INSERT INTO",sql,flags=re.I)
            if is_replace:
                sql += " ON CONFLICT(subscription_id) DO UPDATE SET uid=EXCLUDED.uid,status=EXCLUDED.status,created=EXCLUDED.created,updated=EXCLUDED.updated"
            elif is_ignore:
                sql += " ON CONFLICT DO NOTHING"
            sql=re.sub(r"ALTER TABLE (\w+) ADD COLUMN (\w+)",r"ALTER TABLE \1 ADD COLUMN IF NOT EXISTS \2",sql,flags=re.I)
            sql=sql.replace("?","%s")
        return self.connection.execute(sql,params)
    def executescript(self,script):
        if not self.is_postgres: return self.connection.executescript(script)
        cursor=None
        for statement in script.split(";"):
            if statement.strip(): cursor=self.execute(statement)
        return cursor

def db(): return DatabaseConnection()
def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS refresh_sessions(token_hash TEXT PRIMARY KEY,uid TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL,access_token_hash TEXT,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS rate_limits(bucket_key TEXT PRIMARY KEY,window_start REAL NOT NULL,hits INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS families(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner_uid TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS members(family_id TEXT NOT NULL,uid TEXT NOT NULL,role TEXT NOT NULL,consent INTEGER NOT NULL DEFAULT 0,consent_version TEXT,consent_at REAL,PRIMARY KEY(family_id,uid),FOREIGN KEY(family_id) REFERENCES families(id) ON DELETE CASCADE,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,family_id TEXT NOT NULL,created_by TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS locations(family_id TEXT NOT NULL,uid TEXT NOT NULL,lat REAL NOT NULL,lon REAL NOT NULL,accuracy REAL,captured REAL NOT NULL,battery INTEGER,PRIMARY KEY(family_id,uid),FOREIGN KEY(family_id,uid) REFERENCES members(family_id,uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS devices(device_id TEXT PRIMARY KEY,uid TEXT NOT NULL,device_name TEXT NOT NULL,platform TEXT NOT NULL,created REAL NOT NULL,last_seen REAL NOT NULL,battery INTEGER,lat REAL,lon REAL,accuracy REAL,captured REAL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_devices_uid_last_seen ON devices(uid,last_seen);
        CREATE TABLE IF NOT EXISTS entitlements(uid TEXT PRIMARY KEY,status TEXT NOT NULL,product_id TEXT,base_plan_id TEXT,token_hash TEXT,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS app_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_subscriptions(subscription_id TEXT PRIMARY KEY,uid TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS utr_payments(id TEXT PRIMARY KEY,uid TEXT NOT NULL,utr TEXT NOT NULL UNIQUE,amount_paise INTEGER NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,reviewed REAL,review_note TEXT,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        """)
        refresh_columns={r["name"] for r in c.execute("PRAGMA table_info(refresh_sessions)").fetchall()}
        if "access_token_hash" not in refresh_columns:
            c.execute("ALTER TABLE refresh_sessions ADD COLUMN access_token_hash TEXT")
        columns={r["name"] for r in c.execute("PRAGMA table_info(locations)").fetchall()}
        if "battery" not in columns: c.execute("ALTER TABLE locations ADD COLUMN battery INTEGER")
        entitlement_columns={r["name"] for r in c.execute("PRAGMA table_info(entitlements)").fetchall()}
        if "expires_at" not in entitlement_columns: c.execute("ALTER TABLE entitlements ADD COLUMN expires_at REAL")
        row=c.execute("SELECT v FROM app_meta WHERE k='jwt_secret'").fetchone()
        if not row: c.execute("INSERT INTO app_meta VALUES('jwt_secret',?)",(secrets.token_urlsafe(48),))
init_db()
def secret():
    with db() as c: return c.execute("SELECT v FROM app_meta WHERE k='jwt_secret'").fetchone()["v"].encode()
def b64(x:bytes)->str: return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
def jwt_make(uid:str)->str:
    head=b64(b'{"alg":"HS256","typ":"JWT"}')
    payload=b64(json.dumps({"sub":uid,"iat":int(time.time()),"exp":int(time.time())+3600}).encode())
    msg=f"{head}.{payload}".encode()
    return f"{head}.{payload}.{b64(hmac.new(secret(),msg,hashlib.sha256).digest())}"
def current_uid(authorization: str|None=Header(default=None))->str:
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Bearer access token required")
    token=authorization[7:]
    try:
        h,p,s=token.split("."); msg=f"{h}.{p}".encode()
        if not hmac.compare_digest(b64(hmac.new(secret(),msg,hashlib.sha256).digest()),s): raise ValueError()
        claims=json.loads(base64.urlsafe_b64decode(p+"="*((4-len(p)%4)%4)))
        now=time.time()
        if int(claims["exp"])<now: raise ValueError()
        uid=claims["sub"]
        token_hash=hashlib.sha256(token.encode()).hexdigest()
        with db() as c:
            session=c.execute("SELECT 1 FROM refresh_sessions WHERE uid=? AND access_token_hash=? AND expires>?",(uid,token_hash,now)).fetchone()
            if not session or not c.execute("SELECT 1 FROM users WHERE uid=?",(uid,)).fetchone(): raise ValueError()
        return uid
    except Exception: raise HTTPException(401,"Invalid, revoked, or expired access token")

def enforce_rate_limit(c,bucket_key:str,limit:int,window_seconds:int=900):
    """Atomic database-backed fixed-window limit shared by workers and restarts."""
    now=time.time()
    row=c.execute(
        """INSERT INTO rate_limits(bucket_key,window_start,hits) VALUES(?,?,1)
        ON CONFLICT(bucket_key) DO UPDATE SET
          window_start=CASE WHEN rate_limits.window_start<=? THEN excluded.window_start ELSE rate_limits.window_start END,
          hits=CASE WHEN rate_limits.window_start<=? THEN 1 ELSE rate_limits.hits+1 END
        RETURNING hits""",
        (bucket_key,now,now-window_seconds,now-window_seconds),
    ).fetchone()
    c.execute("DELETE FROM rate_limits WHERE window_start<?",(now-2*window_seconds,))
    if int(row["hits"])>limit:
        raise HTTPException(429,"Too many attempts. Please wait before trying again.",headers={"Retry-After":str(window_seconds)})
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
TRIAL_SECONDS=3*24*60*60

def premium_active(c,uid):
    now=time.time()
    row=c.execute("SELECT status,expires_at FROM entitlements WHERE uid=?",(uid,)).fetchone()
    if row and row["status"]=="verified_active" and (row["expires_at"] is None or row["expires_at"]>now):
        return True
    user=c.execute("SELECT created FROM users WHERE uid=?",(uid,)).fetchone()
    return bool(user and now-float(user["created"])<TRIAL_SECONDS)

def trial_status(c,uid):
    user=c.execute("SELECT created FROM users WHERE uid=?",(uid,)).fetchone()
    if not user: return {"active":False,"endsAt":None,"secondsRemaining":0}
    end=float(user["created"])+TRIAL_SECONDS
    remaining=max(0,int(end-time.time()))
    return {"active":remaining>0,"endsAt":end,"secondsRemaining":remaining}

class _NullContext:
    def __enter__(self): return self
    def __exit__(self,*args): return False

@app.middleware("http")
async def size_limit(request:Request,call_next):
    length=request.headers.get("content-length")
    if length and int(length)>MAX_BODY_BYTES: return JSONResponse(status_code=413,content={"detail":"Request too large"})
    return await call_next(request)
@app.get("/health")
def health():
    try:
        with db() as c:
            c.execute("SELECT 1").fetchone()
            if DB_KIND=="postgresql":
                schema=c.execute("SELECT to_regclass('devices') AS table_name").fetchone()
                table_ready=bool(schema and schema["table_name"])
            else:
                table_ready=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='devices'").fetchone())
        return {"status":"ok","service":"familyguard-api","auth":"database-jwt","storage":DB_KIND,"databaseConnected":True,"deviceTableReady":table_ready,"locationSharing":"consent-gated"}
    except Exception:
        raise HTTPException(503,"Database connection or required schema is unavailable.")
def issue_auth_session(uid:str):
    now=time.time()
    refresh=secrets.token_urlsafe(48)
    access=jwt_make(uid)
    digest=hashlib.sha256(refresh.encode()).hexdigest()
    access_hash=hashlib.sha256(access.encode()).hexdigest()
    with db() as c:
        c.execute("INSERT INTO refresh_sessions(token_hash,uid,expires,created,access_token_hash) VALUES(?,?,?,?,?)",(digest,uid,now+30*24*60*60,now,access_hash))
    return {"idToken":access,"refreshToken":refresh,"localId":uid,"expiresIn":"3600","refreshExpiresIn":"2592000","authProvider":"FamilyGuard"}

@app.post("/auth/register")
def register(payload:AuthIn):
    email=str(payload.email).lower().strip()
    if len(payload.password.get_secret_value())<10: raise HTTPException(400,"Password must contain at least 10 characters")
    uid=secrets.token_urlsafe(18)
    try:
        with db() as c: c.execute("INSERT INTO users VALUES(?,?,?,?)",(uid,email,hash_password(payload.password.get_secret_value()),time.time()))
    except DB_INTEGRITY_ERRORS: raise HTTPException(409,"An account with this email already exists")
    return issue_auth_session(uid)

@app.post("/auth/login")
def login(payload:AuthIn,request:Request):
    email=str(payload.email).lower().strip()
    ip=request.client.host if request.client else "unknown"
    with db() as c:
        enforce_rate_limit(c,"login:ip:"+ip,40)
        enforce_rate_limit(c,"login:identity:"+ip+":"+email,8)
        row=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not row or not check_password(payload.password.get_secret_value(),row["password_hash"]): raise HTTPException(401,"Email or password is incorrect")
    return issue_auth_session(row["uid"])

@app.post("/auth/refresh")
def refresh_auth(payload:RefreshIn):
    now=time.time()
    digest=hashlib.sha256(payload.refreshToken.encode()).hexdigest()
    replacement=secrets.token_urlsafe(48)
    replacement_hash=hashlib.sha256(replacement.encode()).hexdigest()
    with db() as c:
        row=c.execute("SELECT uid,expires FROM refresh_sessions WHERE token_hash=?",(digest,)).fetchone()
        if not row:
            raise HTTPException(401,"Refresh session is invalid. Please sign in again.")
        if row["expires"]<=now:
            c.execute("DELETE FROM refresh_sessions WHERE token_hash=?",(digest,))
            raise HTTPException(401,"Refresh session expired. Please sign in again.")
        uid=row["uid"]
        c.execute("DELETE FROM refresh_sessions WHERE token_hash=?",(digest,))
        access=jwt_make(uid)
        access_hash=hashlib.sha256(access.encode()).hexdigest()
        c.execute("INSERT INTO refresh_sessions(token_hash,uid,expires,created,access_token_hash) VALUES(?,?,?,?,?)",(replacement_hash,uid,now+30*24*60*60,now,access_hash))
    return {"idToken":access,"refreshToken":replacement,"localId":uid,"expiresIn":"3600","refreshExpiresIn":"2592000","authProvider":"FamilyGuard"}

@app.post("/auth/logout")
def logout_auth(payload:RefreshIn):
    digest=hashlib.sha256(payload.refreshToken.encode()).hexdigest()
    with db() as c: c.execute("DELETE FROM refresh_sessions WHERE token_hash=?",(digest,))
    return {"loggedOut":True}

@app.post("/auth/change-password")
def change_password(payload:PasswordChangeIn,uid:str=Depends(current_uid)):
    with db() as c:
        user=c.execute("SELECT password_hash FROM users WHERE uid=?",(uid,)).fetchone()
        if not user or not check_password(payload.currentPassword.get_secret_value(),user["password_hash"]):
            raise HTTPException(401,"Current password is incorrect")
        if payload.currentPassword.get_secret_value()==payload.newPassword.get_secret_value():
            raise HTTPException(400,"New password must be different from the current password")
        c.execute("UPDATE users SET password_hash=? WHERE uid=?",(hash_password(payload.newPassword.get_secret_value()),uid))
        # Revoke every device/session for this account after a credential change.
        c.execute("DELETE FROM refresh_sessions WHERE uid=?",(uid,))
    return {"passwordChanged":True,"sessionsRevoked":True,"message":"Password changed. Please sign in again on your devices."}

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
        row=c.execute("SELECT status,updated,expires_at FROM entitlements WHERE uid=?",(uid,)).fetchone()
        sub=c.execute("SELECT subscription_id,status,updated FROM payment_subscriptions WHERE uid=? ORDER BY updated DESC LIMIT 1",(uid,)).fetchone()
        active=premium_active(c,uid)
        trial=trial_status(c,uid)
        paid=bool(row and row["status"]=="verified_active" and (row["expires_at"] is None or row["expires_at"]>time.time()))
    return {"plan":"premium" if paid else ("trial" if trial["active"] else "free"),"premium":active,"trial":trial,"status":row["status"] if row else ("trial" if trial["active"] else "free"),"subscriptionStatus":sub["status"] if sub else None,"updatedAt":row["updated"] if row else None,"expiresAt":row["expires_at"] if row else None}

@app.get("/account/profile")
def account_profile(uid:str=Depends(current_uid)):
    with db() as c:
        user=c.execute("SELECT uid,email,created FROM users WHERE uid=?",(uid,)).fetchone()
        if not user: raise HTTPException(404,"Account not found")
        entitlement=c.execute("SELECT status,expires_at,updated FROM entitlements WHERE uid=?",(uid,)).fetchone()
        active=premium_active(c,uid)
        trial=trial_status(c,uid)
    paid=bool(entitlement and entitlement["status"]=="verified_active" and (entitlement["expires_at"] is None or entitlement["expires_at"]>time.time()))
    return {"uid":uid,"email":user["email"],"createdAt":user["created"],"plan":"premium" if paid else ("trial" if trial["active"] else "free"),"premium":active,"trial":trial,"entitlementStatus":entitlement["status"] if entitlement else "free","expiresAt":entitlement["expires_at"] if entitlement else None}

@app.get("/family/mine")
def my_families(uid:str=Depends(current_uid)):
    with db() as c:
        rows=c.execute("""SELECT f.id,f.name,f.owner_uid,f.created,m.role,m.consent
        FROM families f JOIN members m ON m.family_id=f.id
        WHERE m.uid=? ORDER BY f.created DESC""",(uid,)).fetchall()
        result=[]
        for family in rows:
            members=c.execute("""SELECT m.uid,u.email,m.role,m.consent,m.consent_at
            FROM members m JOIN users u ON u.uid=m.uid
            WHERE m.family_id=? ORDER BY CASE WHEN m.role='owner' THEN 0 ELSE 1 END,u.email""",(family["id"],)).fetchall()
            result.append({"familyId":family["id"],"name":family["name"],"ownerUid":family["owner_uid"],"createdAt":family["created"],"myRole":family["role"],"members":[{"uid":m["uid"],"email":m["email"],"role":m["role"],"locationConsent":bool(m["consent"]),"consentAt":m["consent_at"]} for m in members]})
    return {"families":result}

@app.post("/family/create")
def create_family(payload:FamilyCreate,uid:str=Depends(current_uid)):
    now=time.time()
    family_id=secrets.token_urlsafe(18)
    with db() as c:
        if not premium_active(c,uid):
            raise HTTPException(402,"Family creation requires an active Premium plan (₹999 / 30 days).")
        c.execute("INSERT INTO families(id,name,owner_uid,created) VALUES(?,?,?,?)",(family_id,payload.displayName.strip(),uid,now))
        c.execute("INSERT INTO members(family_id,uid,role,consent) VALUES(?,?,?,0)",(family_id,uid,"owner"))
        code=None
        for _ in range(10):
            candidate=f"{secrets.randbelow(1000000):06d}"
            try:
                c.execute("INSERT INTO invites(code,family_id,created_by,expires,used) VALUES(?,?,?,?,0)",(candidate,family_id,uid,now+INVITE_TTL_MINUTES*60))
                code=candidate
                break
            except DB_INTEGRITY_ERRORS:
                continue
        if code is None: raise HTTPException(503,"Could not create a unique invite code. Please retry.")
    return {"familyId":family_id,"familyName":payload.displayName.strip(),"inviteCode":code,"expiresInSeconds":INVITE_TTL_MINUTES*60}

@app.post("/family/join")
def join_family(payload:FamilyJoin,request:Request,uid:str=Depends(current_uid)):
    now=time.time()
    ip=request.client.host if request.client else "unknown"
    with db() as c:
        enforce_rate_limit(c,"invite:ip:"+ip,30)
        enforce_rate_limit(c,"invite:user:"+uid+":"+ip,8)
        invite=c.execute("SELECT i.*,f.name FROM invites i JOIN families f ON f.id=i.family_id WHERE i.code=?",(payload.inviteCode,)).fetchone()
        if not invite or invite["used"] or invite["expires"]<=now:
            raise HTTPException(404,"Invite code is invalid, expired, or already used.")
        c.execute("INSERT OR IGNORE INTO members(family_id,uid,role,consent) VALUES(?,?,?,0)",(invite["family_id"],uid,"member"))
        c.execute("UPDATE invites SET used=1 WHERE code=?",(payload.inviteCode,))
    return {"familyId":invite["family_id"],"familyName":invite["name"],"locationConsentRequired":True}

@app.post("/family/consent")
def set_family_consent(payload:ConsentRequest,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        member=membership(c,uid,payload.familyId)
        c.execute("UPDATE members SET consent=?,consent_version=?,consent_at=? WHERE family_id=? AND uid=?",
                  (1 if payload.shareLocation else 0,payload.consentVersion if payload.shareLocation else None,now if payload.shareLocation else None,payload.familyId,uid))
        if not payload.shareLocation:
            c.execute("DELETE FROM locations WHERE family_id=? AND uid=?",(payload.familyId,uid))
            c.execute("UPDATE devices SET lat=NULL,lon=NULL,accuracy=NULL,captured=NULL WHERE uid=?",(uid,))
    return {"familyId":payload.familyId,"consent":payload.shareLocation,"revoked":not payload.shareLocation}

@app.post("/devices/register")
def register_device(payload:DeviceRegister,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        existing=c.execute("SELECT uid FROM devices WHERE device_id=?",(payload.deviceId,)).fetchone()
        if existing and existing["uid"]!=uid:
            raise HTTPException(409,"This device ID is already registered to another account.")
        if existing:
            c.execute("UPDATE devices SET device_name=?,platform=?,last_seen=?,battery=COALESCE(?,battery) WHERE device_id=? AND uid=?",
                (payload.deviceName.strip(),payload.platform.strip().lower(),now,payload.batteryPercent,payload.deviceId,uid))
        else:
            c.execute("INSERT INTO devices(device_id,uid,device_name,platform,created,last_seen,battery) VALUES(?,?,?,?,?,?,?)",
                (payload.deviceId,uid,payload.deviceName.strip(),payload.platform.strip().lower(),now,now,payload.batteryPercent))
    return {"registered":True,"deviceId":payload.deviceId,"lastSeen":now}

@app.get("/devices")
def list_devices(familyId:Optional[str]=None,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        if familyId:
            membership(c,uid,familyId)
            rows=c.execute("""SELECT d.*,u.email,m.uid AS member_uid,m.consent
                FROM devices d JOIN members m ON m.uid=d.uid AND m.family_id=?
                JOIN users u ON u.uid=d.uid ORDER BY d.last_seen DESC""",(familyId,)).fetchall()
        else:
            rows=c.execute("""SELECT d.*,u.email,1 AS consent,d.uid AS member_uid
                FROM devices d JOIN users u ON u.uid=d.uid
                WHERE d.uid=? ORDER BY d.last_seen DESC""",(uid,)).fetchall()
    result=[]
    for r in rows:
        consent=bool(r["consent"])
        if familyId and not consent:
            continue
        item={"deviceId":r["device_id"],"deviceName":r["device_name"],"platform":r["platform"],
            "uid":r["member_uid"],"email":r["email"],"lastSeen":r["last_seen"],
            "online":r["last_seen"]>=now-180,"batteryPercent":r["battery"],
            "locationConsent":consent if familyId else None}
        if (not familyId or consent) and r["lat"] is not None and r["lon"] is not None:
            item["location"]={"latitude":r["lat"],"longitude":r["lon"],
                "accuracyMeters":r["accuracy"],"capturedAt":r["captured"]}
        result.append(item)
    return {"devices":result,"count":len(result),"onlineCount":sum(1 for d in result if d["online"])}

@app.delete("/devices/{device_id}")
def remove_device(device_id:str,uid:str=Depends(current_uid)):
    with db() as c:
        row=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone()
        if not row: raise HTTPException(404,"Device not found on this account.")
        c.execute("DELETE FROM devices WHERE device_id=? AND uid=?",(device_id,uid))
    return {"removed":True,"deviceId":device_id}

@app.post("/location/update")
def update_location(payload:LocationUpdate,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        member=membership(c,uid,payload.familyId)
        if not member["consent"]:
            raise HTTPException(403,"Location sharing requires explicit family consent.")
        captured=payload.capturedAt.timestamp()
        if captured>now+300 or captured<now-24*60*60:
            raise HTTPException(400,"Location timestamp is outside the accepted time window.")
        c.execute("""INSERT INTO locations(family_id,uid,lat,lon,accuracy,captured,battery) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(family_id,uid) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,accuracy=excluded.accuracy,captured=excluded.captured,battery=excluded.battery""",
        (payload.familyId,uid,payload.latitude,payload.longitude,payload.accuracyMeters,captured,payload.batteryPercent))
        if payload.deviceId:
            device=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(payload.deviceId,uid)).fetchone()
            if not device: raise HTTPException(404,"Register this device before sharing its location.")
            c.execute("UPDATE devices SET last_seen=?,battery=?,lat=?,lon=?,accuracy=?,captured=? WHERE device_id=? AND uid=?",
                (now,payload.batteryPercent,payload.latitude,payload.longitude,payload.accuracyMeters,captured,payload.deviceId,uid))
    return {"updated":True,"deviceId":payload.deviceId,"capturedAt":payload.capturedAt.isoformat(),"batteryPercent":payload.batteryPercent}

@app.get("/location/family")
def family_locations(familyId:str,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        membership(c,uid,familyId)
        rows=c.execute("""SELECT l.uid,l.lat,l.lon,l.accuracy,l.captured,l.battery
        FROM locations l JOIN members m ON m.family_id=l.family_id AND m.uid=l.uid
        WHERE l.family_id=? AND m.consent=1 ORDER BY l.captured DESC""",(familyId,)).fetchall()
    return {"familyId":familyId,"locations":[{"uid":r["uid"],"latitude":r["lat"],"longitude":r["lon"],"accuracyMeters":r["accuracy"],"batteryPercent":r["battery"],"capturedAt":datetime.fromtimestamp(r["captured"],timezone.utc).isoformat(),"isOnline":r["captured"]>=now-10*60} for r in rows]}

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
            c.execute("UPDATE entitlements SET expires_at=? WHERE uid=?",(now+30*24*60*60,uid))
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
                c.execute("UPDATE entitlements SET expires_at=? WHERE uid=?",(float(entity.get("current_end") or (now+30*24*60*60)),uid))
            else:
                c.execute("UPDATE entitlements SET status='inactive',updated=? WHERE uid=? AND token_hash=?",(now,uid,sub_id))
    return {"received":True}


class UtrSubmission(BaseModel):
    utr: str=Field(pattern=r"^\d{12}$")

admin_basic=HTTPBasic(auto_error=False)

def require_admin(credentials: HTTPBasicCredentials|None=Depends(admin_basic)):
    username=os.getenv("ADMIN_PANEL_USERNAME","").strip()
    password=os.getenv("ADMIN_PANEL_PASSWORD","")
    if len(password)<16 or not username:
        raise HTTPException(503,"Admin panel is not configured. Set ADMIN_PANEL_USERNAME and a 16+ character ADMIN_PANEL_PASSWORD on the hosting dashboard.")
    if credentials is None or not hmac.compare_digest(credentials.username,username) or not hmac.compare_digest(credentials.password,password):
        raise HTTPException(401,"Admin sign-in required",headers={"WWW-Authenticate":"Basic"})
    return username

def telegram_notify(message: str) -> bool:
    """Best-effort admin alert; Telegram outages never change payment state."""
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID","").strip()
    if not token or not chat_id:
        return False
    body=json.dumps({"chat_id":chat_id,"text":message[:3900],"disable_web_page_preview":True}).encode()
    req=urllib.request.Request(
        "https://api.telegram.org/bot"+token+"/sendMessage",
        data=body,
        method="POST",
        headers={"Content-Type":"application/json","Accept":"application/json"},
    )
    try:
        with urllib.request.urlopen(req,timeout=8) as response:
            result=json.loads(response.read().decode())
            return bool(result.get("ok"))
    except Exception:
        return False

@app.post("/admin/api/telegram/test")
def telegram_test(_:str=Depends(require_admin)):
    if not os.getenv("TELEGRAM_BOT_TOKEN","").strip() or not os.getenv("TELEGRAM_ADMIN_CHAT_ID","").strip():
        raise HTTPException(503,"Set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID in Render Environment.")
    if not telegram_notify("✅ FamilyGuard Telegram test successful. Admin payment alerts are connected."):
        raise HTTPException(502,"Telegram could not deliver the test message. Check the bot token, chat ID, and that you pressed START on your bot.")
    return {"sent":True,"message":"Telegram test message sent."}

@app.get("/payment/upi-config")
def upi_payment_config():
    upi_id=os.getenv("ADMIN_UPI_ID","").strip()
    if not upi_id or "@" not in upi_id:
        return {"enabled":False,"amount":99900,"currency":"INR","message":"UPI payment is not configured. No payment should be sent."}
    payee=os.getenv("ADMIN_UPI_NAME","FamilyGuard").strip()[:60]
    uri="upi://pay?pa="+quote(upi_id,safe="@._-")+"&pn="+quote(payee,safe="")+"&am=999.00&cu=INR&tn="+quote("FamilyGuard Premium 30 days",safe="")
    return {"enabled":True,"upiId":upi_id,"payeeName":payee,"amount":99900,"currency":"INR","upiUri":uri,"mode":"manual_review"}

@app.post("/payment/submit-utr")
def submit_utr(payload:UtrSubmission,uid:str=Depends(current_uid)):
    now=time.time()
    payment_id=secrets.token_urlsafe(18)
    with db() as c:
        if c.execute("SELECT 1 FROM utr_payments WHERE utr=?",(payload.utr,)).fetchone():
            raise HTTPException(409,"This UTR has already been submitted.")
        if c.execute("SELECT 1 FROM utr_payments WHERE uid=? AND status='pending' LIMIT 1",(uid,)).fetchone():
            raise HTTPException(409,"You already have a UTR awaiting review.")
        c.execute("INSERT INTO utr_payments(id,uid,utr,amount_paise,status,created) VALUES(?,?,?,?,?,?)",(payment_id,uid,payload.utr,99900,"pending",now))
        account=c.execute("SELECT email FROM users WHERE uid=?",(uid,)).fetchone()
    telegram_notify("🟠 FamilyGuard: New UTR submitted\nAmount: ₹999\nUTR: "+payload.utr+"\nAccount: "+(account["email"] if account else uid)+"\nStatus: Pending manual bank verification")
    return {"submitted":True,"paymentId":payment_id,"status":"pending","message":"UTR submitted for manual bank-statement verification. Premium is not active yet."}

@app.get("/payment/my-utr")
def my_utr(uid:str=Depends(current_uid)):
    with db() as c:
        rows=c.execute("SELECT id,utr,amount_paise,status,created,reviewed,review_note FROM utr_payments WHERE uid=? ORDER BY created DESC LIMIT 10",(uid,)).fetchall()
    return {"payments":[dict(r) for r in rows]}

@app.get("/admin/api/payments")
def admin_list_payments(status:str="pending",_:str=Depends(require_admin)):
    if status not in ("pending","approved","rejected","all"): raise HTTPException(400,"Invalid payment status")
    with db() as c:
        query="SELECT p.id,p.uid,u.email,p.utr,p.amount_paise,p.status,p.created,p.reviewed,p.review_note FROM utr_payments p JOIN users u ON u.uid=p.uid"
        if status!="all": query+=" WHERE p.status=?"
        query+=" ORDER BY p.created DESC LIMIT 200"
        rows=c.execute(query,() if status=="all" else (status,)).fetchall()
    return {"payments":[dict(r) for r in rows]}

class AdminReview(BaseModel):
    note: str=Field(default="",max_length=240)

@app.post("/admin/api/payments/{payment_id}/approve")
def admin_approve_utr(payment_id:str,payload:AdminReview,_:str=Depends(require_admin)):
    now=time.time()
    with db() as c:
        row=c.execute("SELECT p.*,u.email FROM utr_payments p JOIN users u ON u.uid=p.uid WHERE p.id=?",(payment_id,)).fetchone()
        if not row: raise HTTPException(404,"Payment request not found")
        if row["status"]!="pending": raise HTTPException(409,"This request has already been reviewed")
        c.execute("UPDATE utr_payments SET status='approved',reviewed=?,review_note=? WHERE id=?",(now,payload.note or "Verified against bank statement",payment_id))
        expires=now+30*24*60*60
        c.execute("""INSERT INTO entitlements(uid,status,product_id,base_plan_id,token_hash,updated,expires_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(uid) DO UPDATE SET status='verified_active',product_id=excluded.product_id,base_plan_id=excluded.base_plan_id,token_hash=excluded.token_hash,updated=excluded.updated,expires_at=excluded.expires_at""",
        (row["uid"],"verified_active","familyguard_upi_manual_999","30-days","upi:"+row["utr"],now,expires))
    telegram_notify("✅ FamilyGuard: UTR approved\nAmount: ₹999\nUTR: "+row["utr"]+"\nAccount: "+row["email"]+"\nPremium: Active for 30 days")
    return {"approved":True,"message":"UPI payment approved. Premium enabled for 30 days."}

@app.post("/admin/api/payments/{payment_id}/reject")
def admin_reject_utr(payment_id:str,payload:AdminReview,_:str=Depends(require_admin)):
    with db() as c:
        row=c.execute("SELECT p.status,p.utr,u.email FROM utr_payments p JOIN users u ON u.uid=p.uid WHERE p.id=?",(payment_id,)).fetchone()
        if not row: raise HTTPException(404,"Payment request not found")
        if row["status"]!="pending": raise HTTPException(409,"This request has already been reviewed")
        c.execute("UPDATE utr_payments SET status='rejected',reviewed=?,review_note=? WHERE id=?",(time.time(),payload.note or "Payment could not be verified",payment_id))
    telegram_notify("❌ FamilyGuard: UTR rejected\nUTR: "+row["utr"]+"\nAccount: "+row["email"]+"\nReason: "+(payload.note or "Payment could not be verified"))
    return {"rejected":True,"message":"UTR rejected. Premium was not granted."}

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
