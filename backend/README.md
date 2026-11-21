# FamilyGuard API (SQLite + JWT)

Self-contained FastAPI backend. No Firebase project, Firebase API key, or Firebase Admin SDK is required.

## Render
- Root directory: `backend`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health: `/health`
- API base URL: `https://princehacker.onrender.com`

SQLite uses `FAMILYGUARD_DB_PATH` when configured; otherwise it uses `/var/data/familyguard.sqlite3` if that directory exists, or a local file beside `main.py`. Render free instances have ephemeral filesystems. Attach persistent storage and set `FAMILYGUARD_DB_PATH=/var/data/familyguard.sqlite3` for durable accounts, families and locations. Free-instance restarts/redeploys may erase SQLite data.

## Endpoints
- `POST /auth/register` and `/auth/login`: email/password, scrypt password hashes, signed 12-hour JWT.
- `POST /family/create`: active verified Premium entitlement required.
- `POST /family/join`: single-use six-digit invite code.
- `POST /family/consent`: explicit opt-in/out; revocation deletes last location.
- `POST /location/update`: only consented family members can upload.
- `GET /location/family?familyId=...`: membership and viewer consent required; only fresh opted-in locations.
- `POST /verify-subscription`: if Play Developer credentials are absent, securely hashes and records the token as pending verification. It never grants Premium based on an unverified token.
- `DELETE /account-data`: deletes account and associated records; Google Play billing is separate.

JWT uses HMAC-SHA256. Location sharing is visible, foreground-only, explicitly enabled and revocable. HTTPS is required. This is not end-to-end encryption. Configure persistent storage, backups, rate limiting and complete a security review before public launch.

No Firebase or API key secrets are needed. Google Play service-account credentials are only required to validate actual purchases; without them purchases stay pending and Premium family creation remains locked.
