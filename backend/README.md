# FamilyGuard API (SQLite + JWT)

Self-contained FastAPI backend. No Firebase project, Firebase API key, or Firebase Admin SDK is required.

## Payment mode

The active payment mode is one-time ₹999 UPI with manual Admin verification. Legacy Razorpay recurring checkout, verification and webhook routes are disabled by default and require the explicit `RAZORPAY_ENABLED=true` flag plus provider credentials.

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


## Encrypted backup and recovery
- Configure `BACKUP_ENCRYPTION_KEY` in Render Environment as a random 32-byte key encoded as exactly 64 hexadecimal characters. Keep it in a password manager or separate secrets vault; never commit it or store it beside a backup.
- Sign in to `/admin/` and use **Download encrypted backup**. The endpoint requires the configured Admin Basic Auth and returns an AES-256-GCM authenticated encrypted snapshot containing all account, family, device, entitlement, UTR-review and session tables.
- Backups are manual downloads, not automatic scheduled snapshots. Download on a regular schedule and keep encrypted copies in a separate secure location.
- Recovery requires the same `BACKUP_ENCRYPTION_KEY` and database connection. Run `python backup_restore.py restore /path/to/file.fgbk --confirm-overwrite` from the backend directory (or run the module from the repository root). Restore replaces all records in the selected database. Test recovery against a separate staging database first.
- For SQLite, mount persistent storage and set `FAMILYGUARD_DB_PATH` to that mounted path. For PostgreSQL, configure `DATABASE_URL` to a managed database with its own provider backups. The health endpoint reports storage and backup-configuration warnings; a green application health check alone is not proof of backup recoverability.


## PrinceCircle production payment configuration

- PostgreSQL is selected through `DATABASE_URL`; keep the Render database URL in the service's private environment settings.
- UPI UTR submissions remain pending until an authenticated administrator verifies them. Never mark a UTR paid based only on a client response or screenshot.
- Razorpay recurring billing is disabled by default. To enable it, configure `RAZORPAY_ENABLED=true`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_PLAN_ID`, and `RAZORPAY_WEBHOOK_SECRET` in Render's private environment settings, then register the webhook endpoint and verify its signature. Do not commit live credentials.
- `/health` reports Razorpay and webhook readiness separately. A false readiness value means checkout/webhook verification must not be advertised as active.
- Android Usage Access is a user-controlled special app access. The app must direct the device user to Android Settings and re-check access when the app resumes; it must never attempt to silently grant or bypass the restriction.
