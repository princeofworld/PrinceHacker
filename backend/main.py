"""Self-contained FamilyGuard API using SQLite, scrypt passwords and signed JWTs.
Location sharing is opt-in, visible, time-limited and revocable.
"""
from __future__ import annotations
import os, sqlite3, hashlib, hmac, base64, json, secrets, time, urllib.request, urllib.error, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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
MAX_MEDIA_UPLOAD_BYTES=5*1024*1024
BACKUP_MAGIC=b"FGBACKUP1"
BACKUP_FORMAT_VERSION=1
BACKUP_TABLES=("users","refresh_sessions","rate_limits","families","members","invites","locations","devices","device_locations","sos_alerts","entitlements","app_meta","payment_subscriptions","utr_payments")
BACKUP_COLUMNS={
    "users":("uid","email","password_hash","created"),
    "refresh_sessions":("token_hash","uid","expires","created","access_token_hash"),
    "rate_limits":("bucket_key","window_start","hits"),
    "families":("id","name","owner_uid","created"),
    "members":("family_id","uid","role","consent","consent_version","consent_at"),
    "invites":("code","family_id","created_by","expires","used"),
    "locations":("family_id","uid","lat","lon","accuracy","captured","battery"),
    "devices":("device_id","uid","device_name","platform","created","last_seen","battery","lat","lon","accuracy","captured","location_updated_at","installed_apps","installed_apps_count","screen_time_minutes","today_calls_count","today_calls_duration_sec","contacts_count","location_granted","usage_granted","calls_granted","contacts_granted","apps_sync_enabled","calls_contacts_sync_enabled","summary_last_synced_at","sms_today_count","sms_safety_status"),
    "device_locations":("id","device_id","uid","lat","lon","accuracy","captured","created"),
    "sos_alerts":("id","device_id","uid","message","lat","lon","accuracy","captured","created","status"),
    "entitlements":("uid","status","product_id","base_plan_id","token_hash","updated","expires_at"),
    "app_meta":("k","v"),
    "payment_subscriptions":("subscription_id","uid","status","created","updated"),
    "utr_payments":("id","uid","utr","amount_paise","status","created","reviewed","review_note"),
}
DB_PATH=os.getenv("FAMILYGUARD_DB_PATH", str(Path(os.getenv("RENDER_DISK_PATH","/var/data"))/"familyguard.sqlite3"))
if not os.getenv("RENDER_DISK_PATH") and not Path("/var/data").exists():
    DB_PATH=os.getenv("FAMILYGUARD_DB_PATH", str(Path(__file__).resolve().parent/"familyguard.sqlite3"))
if DB_KIND=="sqlite":
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app=FastAPI(title="PrinceCircle API",version="3.0.0")

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
class ProfileUpdate(BaseModel):
    name: str=Field(default="",max_length=80)
    phone: str=Field(default="",max_length=30)
    class Config:
        extra="forbid"
class DeviceConsentUpdate(BaseModel):
    deviceId: str=Field(min_length=16,max_length=80)
    location: Optional[bool]=None
    battery: Optional[bool]=None
    summaries: Optional[bool]=None
    sms_summary: Optional[bool]=None
    class Config:
        extra="forbid"
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

class SosAlertIn(BaseModel):
    message: str=Field(default="Emergency assistance requested",min_length=1,max_length=500)
    latitude: float=Field(ge=-90,le=90)
    longitude: float=Field(ge=-180,le=180)
    accuracyMeters: Optional[float]=Field(default=None,ge=0,le=100000)
    capturedAt: datetime

class SmsSummaryUpdate(BaseModel):
    sms_today_count: int=Field(ge=0,le=10000)
    sms_safety_status: str=Field(min_length=1,max_length=40)
    class Config:
        extra="forbid"

class DeviceSummaryUpdate(BaseModel):
    """Owner-submitted aggregate device summary; no phone numbers or contact names."""
    installed_apps: list=Field(default_factory=list,max_length=100)
    installed_apps_count: int=Field(default=0,ge=0,le=10000)
    screen_time_minutes: int=Field(default=0,ge=0,le=1440)
    today_calls_count: int=Field(default=0,ge=0,le=10000)
    today_calls_duration_sec: int=Field(default=0,ge=0,le=86400)
    contacts_count: int=Field(default=0,ge=0,le=100000)
    battery_percent: Optional[int]=Field(default=None,ge=0,le=100)
    location_granted: bool=False
    usage_granted: bool=False
    calls_granted: bool=False
    contacts_granted: bool=False
    apps_sync_enabled: bool=False
    calls_contacts_sync_enabled: bool=False
    class Config:
        extra="forbid"

class DeviceAppsUpdate(DeviceSummaryUpdate):
    deviceId: str=Field(min_length=16,max_length=80)

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
        CREATE TABLE IF NOT EXISTS devices(device_id TEXT PRIMARY KEY,uid TEXT NOT NULL,device_name TEXT NOT NULL,platform TEXT NOT NULL,created REAL NOT NULL,last_seen REAL NOT NULL,battery INTEGER,lat REAL,lon REAL,accuracy REAL,captured REAL,location_updated_at REAL,installed_apps TEXT NOT NULL DEFAULT '[]',screen_time_minutes INTEGER NOT NULL DEFAULT 0,today_calls_count INTEGER NOT NULL DEFAULT 0,today_calls_duration_sec INTEGER NOT NULL DEFAULT 0,contacts_count INTEGER NOT NULL DEFAULT 0,location_granted INTEGER NOT NULL DEFAULT 0,usage_granted INTEGER NOT NULL DEFAULT 0,calls_granted INTEGER NOT NULL DEFAULT 0,contacts_granted INTEGER NOT NULL DEFAULT 0,apps_sync_enabled INTEGER NOT NULL DEFAULT 0,calls_contacts_sync_enabled INTEGER NOT NULL DEFAULT 0,summary_last_synced_at REAL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_devices_uid_last_seen ON devices(uid,last_seen);
        CREATE TABLE IF NOT EXISTS entitlements(uid TEXT PRIMARY KEY,status TEXT NOT NULL,product_id TEXT,base_plan_id TEXT,token_hash TEXT,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS app_meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_subscriptions(subscription_id TEXT PRIMARY KEY,uid TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS utr_payments(id TEXT PRIMARY KEY,uid TEXT NOT NULL,utr TEXT NOT NULL UNIQUE,amount_paise INTEGER NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,reviewed REAL,review_note TEXT,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS device_locations(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,uid TEXT NOT NULL,lat REAL NOT NULL,lon REAL NOT NULL,accuracy REAL,captured REAL NOT NULL,created REAL NOT NULL,FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_device_locations_device_captured ON device_locations(device_id,captured DESC);
        CREATE TABLE IF NOT EXISTS sos_alerts(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,uid TEXT NOT NULL,message TEXT NOT NULL,lat REAL NOT NULL,lon REAL NOT NULL,accuracy REAL,captured REAL NOT NULL,created REAL NOT NULL,status TEXT NOT NULL DEFAULT 'active',FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE,FOREIGN KEY(uid) REFERENCES users(uid) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_sos_alerts_uid_created ON sos_alerts(uid,created DESC);
        CREATE TABLE IF NOT EXISTS media_files(id TEXT PRIMARY KEY,uid TEXT NOT NULL,device_id TEXT,mime_type TEXT NOT NULL,filename TEXT NOT NULL,data BYTEA NOT NULL,size_bytes INTEGER NOT NULL,created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_media_files_uid_created ON media_files(uid,created DESC);
        CREATE TABLE IF NOT EXISTS webrtc_sessions(id TEXT PRIMARY KEY,uid TEXT NOT NULL,device_id TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL,status TEXT NOT NULL DEFAULT 'active',ice_servers TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS webrtc_signals(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,uid TEXT NOT NULL,sender_id TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_webrtc_signals_session ON webrtc_signals(session_id,created);
        CREATE TABLE IF NOT EXISTS remote_commands(id TEXT PRIMARY KEY,uid TEXT NOT NULL,device_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created REAL NOT NULL,expires REAL NOT NULL,accepted_at REAL);
        """)
        session_columns={r["name"] for r in c.execute("PRAGMA table_info(webrtc_sessions)").fetchall()}
        if "ice_servers" not in session_columns:
            c.execute("ALTER TABLE webrtc_sessions ADD COLUMN ice_servers TEXT NOT NULL DEFAULT '[]'")
        signal_columns={r["name"] for r in c.execute("PRAGMA table_info(webrtc_signals)").fetchall()}
        if "sender_id" not in signal_columns:
            c.execute("ALTER TABLE webrtc_signals ADD COLUMN sender_id TEXT NOT NULL DEFAULT ''")
        refresh_columns={r["name"] for r in c.execute("PRAGMA table_info(refresh_sessions)").fetchall()}
        if "access_token_hash" not in refresh_columns:
            c.execute("ALTER TABLE refresh_sessions ADD COLUMN access_token_hash TEXT")
        device_columns={r["name"] for r in c.execute("PRAGMA table_info(devices)").fetchall()}
        if "location_updated_at" not in device_columns: c.execute("ALTER TABLE devices ADD COLUMN location_updated_at REAL")
        # Add every dashboard telemetry field to pre-existing installations.
        # Keep this additive and idempotent: never rebuild or truncate user tables.
        device_summary_columns={"installed_apps":"TEXT NOT NULL DEFAULT '[]'","installed_apps_count":"INTEGER NOT NULL DEFAULT 0","screen_time_minutes":"INTEGER NOT NULL DEFAULT 0","today_calls_count":"INTEGER NOT NULL DEFAULT 0","today_calls_duration_sec":"INTEGER NOT NULL DEFAULT 0","contacts_count":"INTEGER NOT NULL DEFAULT 0","location_granted":"INTEGER NOT NULL DEFAULT 0","usage_granted":"INTEGER NOT NULL DEFAULT 0","calls_granted":"INTEGER NOT NULL DEFAULT 0","contacts_granted":"INTEGER NOT NULL DEFAULT 0","apps_sync_enabled":"INTEGER NOT NULL DEFAULT 0","calls_contacts_sync_enabled":"INTEGER NOT NULL DEFAULT 0","summary_last_synced_at":"REAL"}
        device_phase1_columns={"sms_today_count":"INTEGER NOT NULL DEFAULT 0","sms_safety_status":"TEXT NOT NULL DEFAULT 'not_enabled'"}
        for column,definition in {**device_summary_columns,**device_phase1_columns}.items():
            if column not in device_columns:
                c.execute(f"ALTER TABLE devices ADD COLUMN {column} {definition}")
                device_columns.add(column)
        columns={r["name"] for r in c.execute("PRAGMA table_info(locations)").fetchall()}
        if "battery" not in columns: c.execute("ALTER TABLE locations ADD COLUMN battery INTEGER")
        entitlement_columns={r["name"] for r in c.execute("PRAGMA table_info(entitlements)").fetchall()}
        if "expires_at" not in entitlement_columns: c.execute("ALTER TABLE entitlements ADD COLUMN expires_at REAL")
        row=c.execute("SELECT v FROM app_meta WHERE k='jwt_secret'").fetchone()
        if not row: c.execute("INSERT INTO app_meta VALUES('jwt_secret',?)",(secrets.token_urlsafe(48),))
init_db()
def backup_encryption_configured():
    try:
        return len(bytes.fromhex(os.getenv("BACKUP_ENCRYPTION_KEY","").strip()))==32
    except ValueError:
        return False

def backup_encryption_key():
    value=os.getenv("BACKUP_ENCRYPTION_KEY","").strip()
    try:
        key=bytes.fromhex(value)
    except ValueError:
        key=b""
    if len(key)!=32:
        raise HTTPException(503,"Encrypted backups are disabled. Configure BACKUP_ENCRYPTION_KEY as 64 hexadecimal characters in the hosting secret settings.")
    return key

def create_backup_blob():
    key=backup_encryption_key()
    with db() as c:
        tables={table:[dict(row) for row in c.execute("SELECT * FROM "+table).fetchall()] for table in BACKUP_TABLES}
    payload=json.dumps({
        "format_version":BACKUP_FORMAT_VERSION,
        "created_at":datetime.now(timezone.utc).isoformat(),
        "source_database":DB_KIND,
        "tables":tables,
    },separators=(",",":"),sort_keys=True,default=str).encode("utf-8")
    nonce=secrets.token_bytes(12)
    return BACKUP_MAGIC+nonce+AESGCM(key).encrypt(nonce,payload,BACKUP_MAGIC)

def restore_backup_blob(blob:bytes,confirm_overwrite:bool=False):
    if not confirm_overwrite:
        raise HTTPException(400,"Restore requires explicit confirm_overwrite=True because it replaces all current database records.")
    key=backup_encryption_key()
    if not blob.startswith(BACKUP_MAGIC) or len(blob)<len(BACKUP_MAGIC)+12+16:
        raise HTTPException(400,"This is not a valid FamilyGuard encrypted backup.")
    nonce=blob[len(BACKUP_MAGIC):len(BACKUP_MAGIC)+12]
    try:
        payload=json.loads(AESGCM(key).decrypt(nonce,blob[len(BACKUP_MAGIC)+12:],BACKUP_MAGIC))
    except Exception:
        raise HTTPException(400,"Backup authentication failed. Check the encryption key and file integrity.")
    if payload.get("format_version")!=BACKUP_FORMAT_VERSION:
        raise HTTPException(400,"Backup format or database schema is not supported by this version.")
    tables=payload.get("tables",{})
    # Accept format-v1 backups created before Phase 1 history tables and SMS
    # aggregate columns were introduced. Missing additive data restores empty.
    legacy_tables=set(BACKUP_TABLES)-{"device_locations","sos_alerts"}
    if set(tables)==legacy_tables:
        tables["device_locations"]=[]
        tables["sos_alerts"]=[]
    if set(tables)!=set(BACKUP_TABLES):
        raise HTTPException(400,"Backup format or database schema is not supported by this version.")
    for table,rows in tables.items():
        if not isinstance(rows,list):
            raise HTTPException(400,"Backup table data is invalid.")
        for row in rows:
            if not isinstance(row,dict):
                raise HTTPException(400,"Backup columns do not match the supported schema.")
            if table=="devices":
                row.setdefault("sms_today_count",0)
                row.setdefault("sms_safety_status","not_enabled")
                row.setdefault("installed_apps_count",len(row.get("installed_apps") or []))
            if set(row)!=set(BACKUP_COLUMNS[table]):
                raise HTTPException(400,"Backup columns do not match the supported schema.")
    with db() as c:
        if c.is_postgres:
            c.execute("TRUNCATE TABLE "+", ".join(BACKUP_TABLES)+" CASCADE")
        else:
            c.execute("PRAGMA foreign_keys=OFF")
            for table in reversed(BACKUP_TABLES):
                c.execute("DELETE FROM "+table)
        for table in BACKUP_TABLES:
            columns=BACKUP_COLUMNS[table]
            placeholders=",".join("?" for _ in columns)
            statement="INSERT INTO "+table+" ("+",".join(columns)+") VALUES ("+placeholders+")"
            for row in tables[table]:
                c.execute(statement,tuple(row[column] for column in columns))
        if not c.is_postgres:
            violations=c.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise HTTPException(400,"Backup restore failed database foreign-key validation.")
    return {"restored":True,"createdAt":payload["created_at"],"tables":{table:len(rows) for table,rows in tables.items()}}

def secret():
    with db() as c: return c.execute("SELECT v FROM app_meta WHERE k='jwt_secret'").fetchone()["v"].encode()
def b64(x:bytes)->str: return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
def jwt_make(uid:str)->str:
    head=b64(b'{"alg":"HS256","typ":"JWT"}')
    # A unique jti prevents two same-second logins from sharing one access
    # token; revoking one device must not leave its token valid via another row.
    payload=b64(json.dumps({"sub":uid,"jti":secrets.token_urlsafe(18),"iat":int(time.time()),"exp":int(time.time())+3600}).encode())
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
    limit=MAX_MEDIA_UPLOAD_BYTES if request.url.path=="/api/media/upload" and request.method=="POST" else MAX_BODY_BYTES
    if length:
        try: declared=int(length)
        except ValueError: return JSONResponse(status_code=400,content={"detail":"Invalid Content-Length"})
        if declared>limit: return JSONResponse(status_code=413,content={"detail":"Request too large"})
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
        warnings=[]
        if DB_KIND=="sqlite" and not DB_PATH.startswith("/var/data/"):
            warnings.append("SQLite is using a local path; verify a persistent disk is mounted or configure PostgreSQL.")
        if not backup_encryption_configured():
            warnings.append("Encrypted admin backups are disabled until a valid 64-character BACKUP_ENCRYPTION_KEY is configured.")
        return {"status":"ok","service":"princecircle-api","auth":"database-jwt","storage":DB_KIND,"databaseConnected":True,"deviceTableReady":table_ready,"locationSharing":"consent-gated","encryptedBackupsConfigured":backup_encryption_configured(),"backupMode":"manual-admin-download","razorpayEnabled":razorpay_configured(),"razorpayWebhookConfigured":razorpay_configured() and bool(os.getenv("RAZORPAY_WEBHOOK_SECRET","").strip()),"productionWarnings":warnings}
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
        # Serialize consumption of this one-time refresh token across API workers.
        # SQLite obtains its writer reservation before reading; PostgreSQL locks
        # the matching row until this transaction commits or rolls back.
        if c.is_postgres:
            row=c.execute("SELECT uid,expires FROM refresh_sessions WHERE token_hash=? FOR UPDATE",(digest,)).fetchone()
        else:
            c.execute("BEGIN IMMEDIATE")
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
    # The product's production payment mode is manual UPI review. Legacy
    # recurring checkout stays disabled unless an operator explicitly enables it.
    return os.getenv("RAZORPAY_ENABLED","false").strip().lower()=="true" and bool(
        os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET") and os.getenv("RAZORPAY_PLAN_ID")
    )
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
    configured=razorpay_configured()
    return {"provider":"razorpay" if configured else "manual_upi","configured":configured,"amount":99900,"currency":"INR","interval":"monthly","displayPrice":"₹999 / 30 days","keyId":os.getenv("RAZORPAY_KEY_ID") if configured else None}

@app.get("/billing/status")
def billing_status(uid:str=Depends(current_uid)):
    with db() as c:
        row=c.execute("SELECT status,updated,expires_at FROM entitlements WHERE uid=?",(uid,)).fetchone()
        sub=c.execute("SELECT subscription_id,status,updated FROM payment_subscriptions WHERE uid=? ORDER BY updated DESC LIMIT 1",(uid,)).fetchone()
        active=premium_active(c,uid)
        trial=trial_status(c,uid)
        paid=bool(row and row["status"]=="verified_active" and (row["expires_at"] is None or row["expires_at"]>time.time()))
    return {"plan":"premium" if paid else ("trial" if trial["active"] else "free"),"premium":active,"trial":trial,"status":row["status"] if row else ("trial" if trial["active"] else "free"),"subscriptionStatus":sub["status"] if sub else None,"updatedAt":row["updated"] if row else None,"expiresAt":row["expires_at"] if row else None}

def _meta_json(c,key,default):
    row=c.execute("SELECT v FROM app_meta WHERE k=?",(key,)).fetchone()
    if not row: return default
    try:
        value=json.loads(row["v"])
        return value if isinstance(value,dict) else default
    except (TypeError,ValueError):
        return default

def _set_meta_json(c,key,value):
    encoded=json.dumps(value,separators=(",",":"),ensure_ascii=False)
    c.execute("INSERT OR IGNORE INTO app_meta(k,v) VALUES(?,?)",(key,encoded))
    c.execute("UPDATE app_meta SET v=? WHERE k=?",(encoded,key))

def _device_consent(c,uid,device_id):
    row=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone()
    if not row: raise HTTPException(404,"Device not found on this account.")
    return _meta_json(c,"device_consent:"+uid+":"+device_id,{"location":False,"battery":False,"summaries":False})

@app.get("/api/profile")
def api_profile(uid:str=Depends(current_uid)):
    with db() as c:
        user=c.execute("SELECT uid,email,created FROM users WHERE uid=?",(uid,)).fetchone()
        if not user: raise HTTPException(404,"Account not found")
        data=_meta_json(c,"profile:"+uid,{"name":"","phone":""})
    return {"uid":uid,"name":data.get("name",""),"phone":data.get("phone",""),"email":user["email"],"createdAt":user["created"]}

@app.put("/api/profile")
def update_api_profile(payload:ProfileUpdate,uid:str=Depends(current_uid)):
    name=payload.name.strip()
    phone=payload.phone.strip()
    if phone and not re.fullmatch(r"[+0-9() .-]{5,30}",phone):
        raise HTTPException(422,"Enter a valid contact phone number.")
    with db() as c:
        user=c.execute("SELECT uid,email FROM users WHERE uid=?",(uid,)).fetchone()
        if not user: raise HTTPException(404,"Account not found")
        _set_meta_json(c,"profile:"+uid,{"name":name,"phone":phone})
    return {"updated":True,"uid":uid,"name":name,"phone":phone,"email":user["email"]}

@app.get("/api/privacy/consent")
def get_api_privacy_consent(device_id:str,uid:str=Depends(current_uid)):
    with db() as c: data=_device_consent(c,uid,device_id)
    return {"deviceId":device_id,"location":bool(data.get("location")),"battery":bool(data.get("battery")),"summaries":bool(data.get("summaries")),"sms_summary":bool(data.get("sms_summary"))}

@app.post("/api/privacy/consent")
def update_api_privacy_consent(payload:DeviceConsentUpdate,uid:str=Depends(current_uid)):
    with db() as c:
        current=_device_consent(c,uid,payload.deviceId)
        updated={key:(bool(getattr(payload,key)) if getattr(payload,key) is not None else bool(current.get(key,False))) for key in ("location","battery","summaries","sms_summary")}
        _set_meta_json(c,"device_consent:"+uid+":"+payload.deviceId,updated)
        if not updated["location"]:
            c.execute("DELETE FROM locations WHERE uid=?",(uid,))
            c.execute("DELETE FROM device_locations WHERE device_id=? AND uid=?",(payload.deviceId,uid))
            c.execute("UPDATE devices SET lat=NULL,lon=NULL,accuracy=NULL,captured=NULL,location_updated_at=NULL WHERE device_id=? AND uid=?",(payload.deviceId,uid))
        if not updated["battery"]:
            c.execute("UPDATE devices SET battery=NULL WHERE device_id=? AND uid=?",(payload.deviceId,uid))
        if not updated["summaries"]:
            c.execute("UPDATE devices SET installed_apps='[]',installed_apps_count=0,screen_time_minutes=0,today_calls_count=0,today_calls_duration_sec=0,contacts_count=0,summary_last_synced_at=NULL WHERE device_id=? AND uid=?",(payload.deviceId,uid))
        if not updated["sms_summary"]:
            c.execute("UPDATE devices SET sms_today_count=0,sms_safety_status='not_enabled' WHERE device_id=? AND uid=?",(payload.deviceId,uid))
    return {"deviceId":payload.deviceId,**updated}

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
    # Commit rate-limit counters before validating the invite. Invalid-code
    # responses must not roll back the counters, or brute-force attempts bypass
    # the limit on both SQLite and PostgreSQL.
    with db() as c:
        enforce_rate_limit(c,"invite:ip:"+ip,30)
        enforce_rate_limit(c,"invite:user:"+uid+":"+ip,8)
    with db() as c:
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
            c.execute("UPDATE devices SET lat=NULL,lon=NULL,accuracy=NULL,captured=NULL,location_updated_at=NULL WHERE uid=?",(uid,))
    return {"familyId":payload.familyId,"consent":payload.shareLocation,"revoked":not payload.shareLocation}

@app.post("/devices/register")
def register_device(payload:DeviceRegister,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        existing_owner=c.execute("SELECT uid FROM devices WHERE device_id=?",(payload.deviceId,)).fetchone()
        if existing_owner and existing_owner["uid"]!=uid:
            raise HTTPException(409,"This device ID is already registered to another account.")
        consent=_meta_json(c,"device_consent:"+uid+":"+payload.deviceId,{"location":False,"battery":False,"summaries":False})
        existing=c.execute("SELECT uid FROM devices WHERE device_id=?",(payload.deviceId,)).fetchone()
        if existing and existing["uid"]!=uid:
            raise HTTPException(409,"This device ID is already registered to another account.")
        if existing:
            c.execute("UPDATE devices SET device_name=?,platform=?,last_seen=?,battery=COALESCE(?,battery) WHERE device_id=? AND uid=?",
                (payload.deviceName.strip(),payload.platform.strip().lower(),now,payload.batteryPercent if consent.get("battery") else None,payload.deviceId,uid))
        else:
            c.execute("INSERT INTO devices(device_id,uid,device_name,platform,created,last_seen,battery) VALUES(?,?,?,?,?,?,?)",
                (payload.deviceId,uid,payload.deviceName.strip(),payload.platform.strip().lower(),now,now,payload.batteryPercent if consent.get("battery") else None))
    return {"registered":True,"deviceId":payload.deviceId,"lastSeen":now}

@app.post("/api/device/apps")
def update_device_apps(payload:DeviceAppsUpdate,uid:str=Depends(current_uid)):
    summary=DeviceSummaryUpdate(**payload.dict(exclude={"deviceId"}))
    return update_device_summary(payload.deviceId,summary,uid)

@app.post("/api/devices/{device_id}/summary")
@app.post("/devices/{device_id}/summary")
def update_device_summary(device_id:str,payload:DeviceSummaryUpdate,uid:str=Depends(current_uid)):
    # Only the authenticated device owner may write this summary.
    apps=[]
    seen_packages=set()
    for app_entry in payload.installed_apps:
        if isinstance(app_entry,str):
            label=app_entry.strip()
            if not label or len(label)>100:
                raise HTTPException(422,"Each installed app label must be 1-100 characters.")
            item=label
        elif isinstance(app_entry,dict):
            label=str(app_entry.get("name","")).strip()
            package=str(app_entry.get("package","")).strip()
            icon=str(app_entry.get("icon","")).strip()
            if not label or len(label)>100 or len(package)>180:
                raise HTTPException(422,"Each app requires a valid name and optional package.")
            if icon and (len(icon)>8192 or not icon.startswith("data:image/webp;base64,")):
                raise HTTPException(422,"App icons must be small WebP data URLs (maximum 8 KB).")
            item={"name":label}
            if package: item["package"]=package
            if icon: item["icon"]=icon
        else:
            raise HTTPException(422,"installed_apps entries must be labels or app objects.")
        identity=(item.get("package") or item.get("name") if isinstance(item,dict) else item).lower()
        if identity not in seen_packages:
            seen_packages.add(identity)
            apps.append(item)
        if len(apps)>100:
            raise HTTPException(422,"At most 100 installed apps are allowed.")
    with db() as c:
        consent=_device_consent(c,uid,device_id)
        if not consent.get("summaries"):
            raise HTTPException(403,"Device summary upload is disabled by this device's owner.")
        synced_at=time.time()
        c.execute("""UPDATE devices SET installed_apps=?,installed_apps_count=?,screen_time_minutes=?,today_calls_count=?,
            today_calls_duration_sec=?,contacts_count=?,battery=COALESCE(?,battery),location_granted=?,usage_granted=?,
            calls_granted=?,contacts_granted=?,apps_sync_enabled=?,calls_contacts_sync_enabled=?,
            summary_last_synced_at=?,last_seen=? WHERE device_id=? AND uid=?""",
            (json.dumps(list(dict.fromkeys(apps)),ensure_ascii=False) if payload.apps_sync_enabled else "[]",
             payload.installed_apps_count if payload.apps_sync_enabled else 0,
             payload.screen_time_minutes if payload.apps_sync_enabled else 0,
             payload.today_calls_count,payload.today_calls_duration_sec,payload.contacts_count,payload.battery_percent if consent.get("battery") else None,
             int(payload.location_granted),int(payload.usage_granted),int(payload.calls_granted),int(payload.contacts_granted),
             int(payload.apps_sync_enabled),int(payload.calls_contacts_sync_enabled),synced_at,synced_at,device_id,uid))
    return {"updated":True,"deviceId":device_id}

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
            "locationConsent":consent if familyId else None,
            "locationUpdatedAt":r["location_updated_at"]}
        # Summary data is restricted to the authenticated owner, not family viewers.
        if not familyId and r["uid"]==uid:
            try:
                app_labels=json.loads(r["installed_apps"] or "[]")
                if not isinstance(app_labels,list): app_labels=[]
            except (TypeError,ValueError):
                app_labels=[]
            item.update({"installed_apps":app_labels,"installed_apps_count":r["installed_apps_count"] or 0,
                "screen_time_minutes":r["screen_time_minutes"] or 0,
                "today_calls_count":r["today_calls_count"] or 0,
                "today_calls_duration_sec":r["today_calls_duration_sec"] or 0,
                "contacts_count":r["contacts_count"] or 0,
                "location_granted":bool(r["location_granted"]),"usage_granted":bool(r["usage_granted"]),
                "calls_granted":bool(r["calls_granted"]),"contacts_granted":bool(r["contacts_granted"]),
                "apps_sync_enabled":bool(r["apps_sync_enabled"]),
                "calls_contacts_sync_enabled":bool(r["calls_contacts_sync_enabled"]),
                "summary_last_synced_at":r["summary_last_synced_at"],
                "sms_today_count":r["sms_today_count"] or 0,
                "sms_safety_status":r["sms_safety_status"] or "not_enabled"})
        if (not familyId or consent) and r["lat"] is not None and r["lon"] is not None:
            item["location"]={"latitude":r["lat"],"longitude":r["lon"],
                "accuracyMeters":r["accuracy"],"capturedAt":r["captured"],
                "updatedAt":r["location_updated_at"]}
        result.append(item)
    return {"devices":result,"count":len(result),"onlineCount":sum(1 for d in result if d["online"])}


@app.post("/api/devices/{device_id}/sos")
def create_sos_alert(device_id:str,payload:SosAlertIn,uid:str=Depends(current_uid)):
    now=time.time()
    captured=payload.capturedAt.timestamp()
    if captured>now+300 or captured<now-10*60:
        raise HTTPException(400,"SOS location must be freshly captured within the last 10 minutes.")
    with db() as c:
        consent=_device_consent(c,uid,device_id)
        if not consent.get("location"):
            raise HTTPException(403,"Enable location sharing consent before sending an SOS location.")
        device=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone()
        if not device: raise HTTPException(404,"Device not found on this account.")
        alert_id=secrets.token_urlsafe(18)
        c.execute("INSERT INTO sos_alerts(id,device_id,uid,message,lat,lon,accuracy,captured,created,status) VALUES(?,?,?,?,?,?,?,?,?,'active')",
            (alert_id,device_id,uid,payload.message.strip(),payload.latitude,payload.longitude,payload.accuracyMeters,captured,now))
    return {"sent":True,"alertId":alert_id,"deviceId":device_id,"status":"active","createdAt":now}

def _sos_device_owner(c,uid:str,device_id:str,family_id:Optional[str]=None)->str:
    device=c.execute("SELECT uid FROM devices WHERE device_id=?",(device_id,)).fetchone()
    if not device: raise HTTPException(404,"Device not found.")
    owner_uid=device["uid"]
    if owner_uid==uid: return owner_uid
    if not family_id: raise HTTPException(404,"Device not found on this account.")
    membership(c,uid,family_id)
    owner_member=c.execute("SELECT consent FROM members WHERE family_id=? AND uid=?",(family_id,owner_uid)).fetchone()
    if not owner_member or not owner_member["consent"]:
        raise HTTPException(404,"Device is not an opted-in member of this family.")
    return owner_uid

@app.get("/api/devices/{device_id}/sos")
def list_sos_alerts(device_id:str,limit:int=50,familyId:Optional[str]=None,uid:str=Depends(current_uid)):
    if limit<1 or limit>200:
        raise HTTPException(422,"limit must be between 1 and 200.")
    with db() as c:
        owner_uid=_sos_device_owner(c,uid,device_id,familyId)
        rows=c.execute("SELECT id,message,lat,lon,accuracy,captured,created,status FROM sos_alerts WHERE device_id=? AND uid=? ORDER BY created DESC LIMIT ?",
            (device_id,owner_uid,limit)).fetchall()
    return {"deviceId":device_id,"alerts":[{"alertId":r["id"],"message":r["message"],"latitude":r["lat"],"longitude":r["lon"],"accuracyMeters":r["accuracy"],"capturedAt":r["captured"],"createdAt":r["created"],"status":r["status"]} for r in rows],"count":len(rows)}

@app.post("/api/devices/{device_id}/sos/{alert_id}/resolve")
def resolve_sos_alert(device_id:str,alert_id:str,familyId:Optional[str]=None,uid:str=Depends(current_uid)):
    with db() as c:
        owner_uid=_sos_device_owner(c,uid,device_id,familyId)
        alert=c.execute("SELECT status FROM sos_alerts WHERE id=? AND device_id=? AND uid=?",(alert_id,device_id,owner_uid)).fetchone()
        if not alert: raise HTTPException(404,"SOS alert not found on this account.")
        if alert["status"]=="resolved": return {"resolved":True,"alertId":alert_id,"status":"resolved"}
        c.execute("UPDATE sos_alerts SET status='resolved' WHERE id=? AND device_id=? AND uid=?",(alert_id,device_id,owner_uid))
    return {"resolved":True,"alertId":alert_id,"status":"resolved"}

@app.get("/api/devices/{device_id}/locations")
def get_device_location_history(device_id:str,limit:int=100,uid:str=Depends(current_uid)):
    if limit<1 or limit>500: raise HTTPException(422,"limit must be between 1 and 500.")
    with db() as c:
        consent=_device_consent(c,uid,device_id)
        if not consent.get("location"):
            raise HTTPException(403,"Location history is unavailable while location consent is off.")
        rows=c.execute("SELECT id,lat,lon,accuracy,captured,created FROM device_locations WHERE device_id=? AND uid=? ORDER BY captured DESC LIMIT ?",
            (device_id,uid,limit)).fetchall()
    return {"deviceId":device_id,"locations":[{"id":r["id"],"latitude":r["lat"],"longitude":r["lon"],"accuracyMeters":r["accuracy"],"capturedAt":r["captured"],"createdAt":r["created"]} for r in rows],"count":len(rows)}

@app.post("/api/devices/{device_id}/sms-summary")
def update_sms_summary(device_id:str,payload:SmsSummaryUpdate,uid:str=Depends(current_uid)):
    allowed_status={"safe","review","alert","unknown"}
    if payload.sms_safety_status not in allowed_status:
        raise HTTPException(422,"sms_safety_status must be safe, review, alert, or unknown.")
    with db() as c:
        consent=_device_consent(c,uid,device_id)
        if not consent.get("sms_summary"):
            raise HTTPException(403,"SMS aggregate summary is disabled by this device's owner.")
        device=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone()
        if not device: raise HTTPException(404,"Device not found on this account.")
        c.execute("UPDATE devices SET sms_today_count=?,sms_safety_status=?,last_seen=? WHERE device_id=? AND uid=?",
            (payload.sms_today_count,payload.sms_safety_status,time.time(),device_id,uid))
    return {"updated":True,"deviceId":device_id,"sms_today_count":payload.sms_today_count,"sms_safety_status":payload.sms_safety_status}

@app.get("/api/devices/{device_id}/sms-summary")
def get_sms_summary(device_id:str,uid:str=Depends(current_uid)):
    with db() as c:
        consent=_device_consent(c,uid,device_id)
        if not consent.get("sms_summary"):
            raise HTTPException(403,"SMS aggregate summary is disabled by this device's owner.")
        row=c.execute("SELECT sms_today_count,sms_safety_status FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone()
        if not row: raise HTTPException(404,"Device not found on this account.")
    return {"deviceId":device_id,"sms_today_count":row["sms_today_count"],"sms_safety_status":row["sms_safety_status"]}

@app.delete("/devices/{device_id}")
def remove_device(device_id:str,uid:str=Depends(current_uid)):
    with db() as c:
        row=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone()
        if not row: raise HTTPException(404,"Device not found on this account.")
        c.execute("DELETE FROM devices WHERE device_id=? AND uid=?",(device_id,uid))
    return {"removed":True,"deviceId":device_id}

@app.post("/devices/location")
@app.post("/location/update")
def update_location(payload:LocationUpdate,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        if not payload.deviceId:
            raise HTTPException(400,"A registered device ID is required for location telemetry.")
        consent=_device_consent(c,uid,payload.deviceId)
        if not consent.get("location"):
            raise HTTPException(403,"Location upload is disabled by this device's owner.")
        member=membership(c,uid,payload.familyId)
        if not member["consent"]:
            raise HTTPException(403,"Location sharing requires explicit family consent.")
        captured=payload.capturedAt.timestamp()
        if captured>now+300 or captured<now-24*60*60:
            raise HTTPException(400,"Location timestamp is outside the accepted time window.")
        c.execute("""INSERT INTO locations(family_id,uid,lat,lon,accuracy,captured,battery) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(family_id,uid) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,accuracy=excluded.accuracy,captured=excluded.captured,battery=excluded.battery""",
        (payload.familyId,uid,payload.latitude,payload.longitude,payload.accuracyMeters,captured,payload.batteryPercent if consent.get("battery") else None))
        if payload.deviceId:
            device=c.execute("SELECT device_id FROM devices WHERE device_id=? AND uid=?",(payload.deviceId,uid)).fetchone()
            if not device: raise HTTPException(404,"Register this device before sharing its location.")
            c.execute("UPDATE devices SET last_seen=?,battery=?,lat=?,lon=?,accuracy=?,captured=?,location_updated_at=? WHERE device_id=? AND uid=?",
                (now,payload.batteryPercent if consent.get("battery") else None,payload.latitude,payload.longitude,payload.accuracyMeters,captured,now,payload.deviceId,uid))
            history_id=secrets.token_urlsafe(18)
            c.execute("INSERT INTO device_locations(id,device_id,uid,lat,lon,accuracy,captured,created) VALUES(?,?,?,?,?,?,?,?)",
                (history_id,payload.deviceId,uid,payload.latitude,payload.longitude,payload.accuracyMeters,captured,now))
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
    if not razorpay_configured(): raise HTTPException(503,"Razorpay recurring checkout is disabled. Submit a UPI UTR for manual Admin review.")
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
    if not razorpay_configured(): raise HTTPException(503,"Razorpay recurring checkout is disabled. Manual UPI review remains the active payment mode.")
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
        try:
            # The UNIQUE constraint is the final guard against simultaneous
            # duplicate submissions across workers and database connections.
            c.execute("INSERT INTO utr_payments(id,uid,utr,amount_paise,status,created) VALUES(?,?,?,?,?,?)",(payment_id,uid,payload.utr,99900,"pending",now))
        except DB_INTEGRITY_ERRORS:
            raise HTTPException(409,"This UTR has already been submitted.")
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

@app.get("/admin/api/backup")
def admin_download_backup(_:str=Depends(require_admin)):
    blob=create_backup_blob()
    filename="familyguard-backup-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".fgbk"
    return Response(content=blob,media_type="application/octet-stream",headers={"Content-Disposition":'attachment; filename="'+filename+'"'})

@app.get("/app/latest-version")
def latest_app_version():
    return {"versionName":os.getenv("APP_VERSION_NAME","1.3.3"),"versionCode":int(os.getenv("APP_VERSION_CODE","8")),"minimumVersionCode":int(os.getenv("APP_MINIMUM_VERSION_CODE","5")),"apkUrl":os.getenv("APP_APK_URL","https://github.com/princeofworld/PrinceHacker/releases/latest/download/PrinceCircle-release-signed.apk"),"releaseNotes":"PrinceCircle update. Review the changes before installing.","forceUpdate":False}

@app.delete("/account-data")
def delete_account_data(uid:str=Depends(current_uid)):
    with db() as c:
        # Transfer each owned family to a remaining member, or remove an empty
        # family, before the account's membership rows are cascaded away.
        owned_families=c.execute("SELECT id FROM families WHERE owner_uid=?",(uid,)).fetchall()
        for family in owned_families:
            successor=c.execute(
                "SELECT uid FROM members WHERE family_id=? AND uid<>? ORDER BY uid LIMIT 1",
                (family["id"],uid),
            ).fetchone()
            if successor:
                c.execute("UPDATE families SET owner_uid=? WHERE id=?",(successor["uid"],family["id"]))
                c.execute("UPDATE members SET role='owner' WHERE family_id=? AND uid=?",(family["id"],successor["uid"]))
            else:
                c.execute("DELETE FROM families WHERE id=?",(family["id"],))
        c.execute("DELETE FROM users WHERE uid=?",(uid,))
    return {"deleted":True,"billingManagedBy":"manual_upi","note":"Direct UPI payments are one-time and do not create recurring mandates."}
@app.get("/api")
def root(): return {"service":"FamilyGuard API","docs":"/docs","health":"/health"}


@app.get("/PRIVACY_POLICY.md",include_in_schema=False)
def privacy_policy_document():
    return FileResponse(Path(__file__).resolve().parent.parent/"PRIVACY_POLICY.md",media_type="text/markdown")

@app.get("/TERMS_OF_SERVICE.md",include_in_schema=False)
def terms_document():
    return FileResponse(Path(__file__).resolve().parent.parent/"TERMS_OF_SERVICE.md",media_type="text/markdown")


# Friendly aliases used by the live deployment smoke tests and browser bookmarks.
@app.get("/", include_in_schema=False)
def home_page_alias():
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")

@app.get("/login", include_in_schema=False)
def login_page_alias():
    return FileResponse(WEB_DIR / "login.html", media_type="text/html")

@app.get("/dashboard", include_in_schema=False)
def dashboard_page_alias():
    return FileResponse(WEB_DIR / "dashboard.html", media_type="text/html")

@app.get("/admin", include_in_schema=False)
def admin_page_alias():
    return FileResponse(WEB_DIR / "admin" / "index.html", media_type="text/html")

@app.get("/admin/", include_in_schema=False)
def admin_page_slash_alias():
    return FileResponse(WEB_DIR / "admin" / "index.html", media_type="text/html")


# Consent-first media, signaling, and remote notification APIs.
@app.post("/api/media/upload")
async def upload_media(request:Request, uid:str=Depends(current_uid)):
    mime=(request.headers.get("content-type") or "").split(";")[0].lower()
    if mime not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Only JPEG, PNG, and WebP photos are supported.")
    raw=await request.body()
    if not raw or len(raw)>MAX_MEDIA_UPLOAD_BYTES: raise HTTPException(413,"Photo must be between 1 byte and 5 MB.")
    device_id=request.headers.get("x-familyguard-device","")[:80] or None
    filename=(request.headers.get("x-file-name","photo") or "photo").replace("\\","_").replace("/","_")[:120]
    media_id=secrets.token_urlsafe(18); now=time.time()
    with db() as c:
        if device_id and not c.execute("SELECT 1 FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone(): raise HTTPException(404,"Device not found.")
        c.execute("INSERT INTO media_files(id,uid,device_id,mime_type,filename,data,size_bytes,created) VALUES(?,?,?,?,?,?,?,?)",(media_id,uid,device_id,mime,filename,raw,len(raw),now))
    return {"id":media_id,"filename":filename,"mimeType":mime,"sizeBytes":len(raw),"createdAt":now}

@app.get("/api/media")
def list_media(uid:str=Depends(current_uid)):
    with db() as c: rows=c.execute("SELECT id,device_id,mime_type,filename,size_bytes,created FROM media_files WHERE uid=? ORDER BY created DESC LIMIT 100",(uid,)).fetchall()
    return {"items":[{"id":r["id"],"deviceId":r["device_id"],"mimeType":r["mime_type"],"filename":r["filename"],"sizeBytes":r["size_bytes"],"createdAt":r["created"]} for r in rows]}

@app.get("/api/media/{media_id}")
def view_media(media_id:str,uid:str=Depends(current_uid)):
    with db() as c: row=c.execute("SELECT mime_type,data FROM media_files WHERE id=? AND uid=?",(media_id,uid)).fetchone()
    if not row: raise HTTPException(404,"Photo not found.")
    return Response(content=bytes(row["data"]),media_type=row["mime_type"],headers={"Cache-Control":"private, no-store","Content-Disposition":"inline"})

@app.delete("/api/media/{media_id}")
def delete_media(media_id:str,uid:str=Depends(current_uid)):
    with db() as c:
        cur=c.execute("DELETE FROM media_files WHERE id=? AND uid=?",(media_id,uid))
        if cur.rowcount!=1: raise HTTPException(404,"Photo not found.")
    return {"deleted":True}

def _owned_device(c,uid,device_id):
    if not c.execute("SELECT 1 FROM devices WHERE device_id=? AND uid=?",(device_id,uid)).fetchone(): raise HTTPException(404,"Device not found.")

@app.post("/api/webrtc/sessions")
def create_webrtc_session(payload:dict,uid:str=Depends(current_uid)):
    device_id=str(payload.get("deviceId",""))[:80]
    if not device_id or payload.get("consentAccepted") is not True: raise HTTPException(400,"Explicit on-device consent is required before live sharing.")
    domain=os.getenv("METERED_DOMAIN","").strip().removeprefix("https://").removeprefix("http://").strip("/")
    secret_key=os.getenv("METERED_SECRET_KEY","").strip()
    if not domain or not secret_key: raise HTTPException(503,"Live sharing is not configured.")
    with db() as c:
        _owned_device(c,uid,device_id)
        enforce_rate_limit(c,"webrtc:create:"+uid+":"+device_id,10)
    endpoint="https://"+domain+"/api/v1/turn/credential?secretKey="+quote(secret_key,safe="")
    body=json.dumps({"expiryInSeconds":3600,"label":"familyguard-"+secrets.token_hex(6)}).encode()
    req=urllib.request.Request(endpoint,data=body,headers={"Content-Type":"application/json","Accept":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=12) as response: credential=json.loads(response.read(65536))
        api_key=credential.get("apiKey")
        if not api_key: raise ValueError("Missing Metered API key")
        get_url="https://"+domain+"/api/v1/turn/credentials?apiKey="+quote(str(api_key),safe="")
        with urllib.request.urlopen(get_url,timeout=12) as response: ice=json.loads(response.read(65536))
        if not isinstance(ice,list) or not ice: raise ValueError("Missing ICE servers")
    except Exception: raise HTTPException(502,"Could not obtain temporary ICE credentials from Metered.")
    sid=secrets.token_urlsafe(24); now=time.time(); expires=now+600
    with db() as c: c.execute("INSERT INTO webrtc_sessions(id,uid,device_id,expires,created,status,ice_servers) VALUES(?,?,?,?,?,'active',?)",(sid,uid,device_id,expires,now,json.dumps(ice,separators=(",",":"))))
    return {"sessionId":sid,"expiresAt":expires,"iceServers":ice}

@app.get("/api/webrtc/sessions")
def list_webrtc_sessions(uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        rows=c.execute("SELECT id,device_id,created,expires,ice_servers FROM webrtc_sessions WHERE uid=? AND status='active' AND expires>? ORDER BY created DESC LIMIT 10",(uid,now)).fetchall()
    return {"sessions":[{"sessionId":r["id"],"deviceId":r["device_id"],"createdAt":r["created"],"expiresAt":r["expires"],"iceServers":json.loads(r["ice_servers"])} for r in rows]}

@app.post("/api/webrtc/{session_id}/signals")
def post_webrtc_signal(session_id:str,payload:dict,uid:str=Depends(current_uid)):
    kind=str(payload.get("kind","")); data=payload.get("payload")
    sender_id=str(payload.get("senderId",""))[:80]
    if not sender_id or kind not in {"offer","answer","ice","end"} or not isinstance(data,(dict,list,str)) or len(json.dumps(data))>12000: raise HTTPException(400,"Invalid signaling message.")
    with db() as c:
        s=c.execute("SELECT uid,expires,status FROM webrtc_sessions WHERE id=?",(session_id,)).fetchone()
        if not s or s["uid"]!=uid or s["expires"]<time.time() or s["status"]!="active": raise HTTPException(404,"Active session not found.")
        enforce_rate_limit(c,"webrtc:signal:"+uid+":"+session_id,120)
        c.execute("INSERT INTO webrtc_signals(id,session_id,uid,sender_id,kind,payload,created) VALUES(?,?,?,?,?,?,?)",(secrets.token_urlsafe(16),session_id,uid,sender_id,kind,json.dumps(data,separators=(",",":")),time.time()))
        if kind=="end": c.execute("UPDATE webrtc_sessions SET status='ended' WHERE id=?",(session_id,))
    return {"accepted":True}

@app.get("/api/webrtc/{session_id}/signals")
def get_webrtc_signals(session_id:str,sender_id:str,uid:str=Depends(current_uid)):
    sender_id=sender_id[:80]
    if not sender_id: raise HTTPException(400,"A signaling client ID is required.")
    with db() as c:
        s=c.execute("SELECT uid,expires,status FROM webrtc_sessions WHERE id=?",(session_id,)).fetchone()
        if not s or s["uid"]!=uid or s["expires"]<time.time() or s["status"]!="active":
            raise HTTPException(404,"Active session not found.")
        rows=c.execute("SELECT id,sender_id,kind,payload,created FROM webrtc_signals WHERE session_id=? AND sender_id<>? ORDER BY created LIMIT 50",(session_id,sender_id)).fetchall()
    return {"signals":[{"signalId":r["id"],"senderId":r["sender_id"],"kind":r["kind"],"payload":json.loads(r["payload"]),"createdAt":r["created"]} for r in rows]}

@app.post("/api/devices/{device_id}/commands")
def enqueue_remote_command(device_id:str,payload:dict,uid:str=Depends(current_uid)):
    kind=str(payload.get("kind",""))
    if kind not in {"alert","ring","in_app_lock_request"}: raise HTTPException(400,"Unsupported remote command.")
    if kind=="in_app_lock_request" and payload.get("requiresLocalAcceptance") is not True: raise HTTPException(400,"Temporary in-app lock requires explicit local acceptance.")
    message=str(payload.get("message","PrinceCircle alert"))[:180]
    try: ttl_seconds=int(payload.get("ttlSeconds",120))
    except (TypeError,ValueError): raise HTTPException(400,"ttlSeconds must be an integer.")
    now=time.time(); cid=secrets.token_urlsafe(18); expires=now+min(300,max(30,ttl_seconds))
    with db() as c:
        _owned_device(c,uid,device_id)
        enforce_rate_limit(c,"remote-command:"+uid+":"+device_id,30)
        c.execute("INSERT INTO remote_commands(id,uid,device_id,kind,payload,status,created,expires) VALUES(?,?,?,?,?,'pending',?,?)",(cid,uid,device_id,kind,json.dumps({"message":message,"requiresLocalAcceptance":kind=="in_app_lock_request"}),now,expires))
    return {"commandId":cid,"status":"pending","expiresAt":expires,"note":"Android OS restrictions are not changed; an in-app lock request requires acceptance on the device."}

@app.get("/api/devices/{device_id}/commands")
def poll_remote_commands(device_id:str,uid:str=Depends(current_uid)):
    now=time.time()
    with db() as c:
        _owned_device(c,uid,device_id)
        c.execute("UPDATE remote_commands SET status='expired' WHERE device_id=? AND uid=? AND status='pending' AND expires<?",(device_id,uid,now))
        rows=c.execute("SELECT id,kind,payload,expires FROM remote_commands WHERE device_id=? AND uid=? AND status IN ('pending','delivered') AND expires>=? ORDER BY created LIMIT 20",(device_id,uid,now)).fetchall()
        c.execute("UPDATE remote_commands SET status='delivered' WHERE device_id=? AND uid=? AND status='pending' AND expires>=?",(device_id,uid,now))
    return {"commands":[{"commandId":r["id"],"kind":r["kind"],"payload":json.loads(r["payload"]),"expiresAt":r["expires"]} for r in rows]}

@app.post("/api/devices/{device_id}/commands/{command_id}/ack")
def acknowledge_remote_command(device_id:str,command_id:str,payload:dict,uid:str=Depends(current_uid)):
    status=str(payload.get("status",""))
    if status not in {"accepted","declined","completed"}: raise HTTPException(400,"Invalid acknowledgement.")
    with db() as c:
        _owned_device(c,uid,device_id)
        cur=c.execute("UPDATE remote_commands SET status=?,accepted_at=? WHERE id=? AND device_id=? AND uid=? AND expires>=? AND status IN ('pending','delivered')",(status,time.time(),command_id,device_id,uid,time.time()))
        if cur.rowcount!=1: raise HTTPException(404,"Command is no longer active.")
    return {"acknowledged":True,"status":status}



# Keep the catch-all static dashboard mount last so API routes above remain reachable.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="familyguard-web")
