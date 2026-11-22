# FamilyGuard Website & Direct APK Deployment

This project is distributed from its own Render website. It does not build an Android App Bundle or publish through Google Play.

## Render service
The FastAPI service serves the public website from `web/` at the root path and serves API routes on the same host. Render should deploy the repository's current `main` branch using the existing backend start command. Keep a persistent disk mounted and set `FAMILYGUARD_DB_PATH` to a path on that disk so accounts and subscription records survive redeploys.

## Custom domain (mobile-browser steps)
1. Open the Render Dashboard: https://dashboard.render.com/
2. Open the FamilyGuard web service, then **Settings → Custom Domains → Add Custom Domain**.
3. Enter the domain you own. Render displays the exact DNS records required for that domain.
4. Open your domain registrar's DNS settings and add those exact records. Wait until Render reports the domain as verified and HTTPS is active.
5. The website uses same-origin API requests, so the PWA dashboard also works on the custom domain. The Android app continues to use the Render service URL unless its API base URL is changed in a later build.

## Optional legacy Razorpay recurring subscription configuration (disabled by default)

The current FamilyGuard payment model is direct one-time UPI with Admin bank-statement verification. Legacy Razorpay routes are disabled unless `RAZORPAY_ENABLED=true` is explicitly configured. Leave this flag false when using PhonePe/Google Pay UPI. The remaining provider setup instructions below are for a future authorized migration only.
A parent/legal guardian or authorized business representative must own and manage the payment-provider merchant account and confirm applicable legal, tax, consumer-protection and recurring-mandate requirements. Never put API secrets in source code, public GitHub files, the APK or browser JavaScript.

Only an authorized business operator should enable this legacy option after opening a merchant account and reviewing applicable recurring-mandate requirements. Configure `RAZORPAY_ENABLED=true` plus these variables in **Render Dashboard → Service → Environment** only after the merchant account and monthly plan have been approved:
- `RAZORPAY_KEY_ID` — public checkout key ID (still configure server-side).
- `RAZORPAY_KEY_SECRET` — private API/signature secret.
- `RAZORPAY_PLAN_ID` — ID of the Razorpay plan set to INR 999 and monthly.
- `RAZORPAY_WEBHOOK_SECRET` — private webhook signing secret.
- `RAZORPAY_SUBSCRIPTION_CYCLES` — optional billing-cycle count; default is 120.

Set the Razorpay webhook URL to `https://princehacker.onrender.com/payment/webhook` (or your verified custom domain plus `/payment/webhook`). Enable subscription events including `subscription.authenticated`, `subscription.activated`, `subscription.charged`, `subscription.cancelled`, `subscription.halted`, `subscription.paused` and `subscription.completed`. Test the full flow in the provider's test mode before accepting real payments. Without all required keys and plan ID, checkout remains unavailable and no payment is taken. There is no manual or fake Premium unlock.

## Signed APK releases
GitHub Actions builds and publishes only `FamilyGuard-release-signed.apk`. It requires all four GitHub Actions repository secrets:
- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

Open GitHub → repository → **Settings → Secrets and variables → Actions** to manage secrets. Do not paste signing secrets into chat, issues, commits or public files. If signing secrets are missing or invalid, the workflow fails and does not publish an unsigned file as a signed APK.

## App update endpoint
`GET /app/latest-version` returns the current version code/name and the official APK URL. Optional Render environment variables:
- `APP_VERSION_NAME` (default `1.3.1`)
- `APP_VERSION_CODE` (default `6`)
- `APP_MINIMUM_VERSION_CODE` (default `5`)
- `APP_APK_URL` (default points to the latest signed GitHub release asset)

The Android app checks this endpoint and opens the official APK URL in the browser when a newer version is available. Android handles the user's download and install confirmation.


## Direct UPI and Admin Panel
Configure these values in Render Dashboard → FamilyGuard service → Environment (never commit the UPI ID or admin password into GitHub):
- `ADMIN_UPI_ID` — the public UPI ID used to receive the ₹999 payment.
- `ADMIN_UPI_NAME` — optional payee display name.
- `ADMIN_PANEL_USERNAME` — admin sign-in username.
- `ADMIN_PANEL_PASSWORD` — a unique password of at least 16 characters. Keep it private; do not share it in chat or commit it.

After redeploy, open `https://princehacker.onrender.com/admin/` in a mobile browser and sign in. The page is only an interface; all payment-list and approve/reject endpoints require HTTP Basic authentication over HTTPS. Compare the exact 12-digit UTR and ₹999 incoming credit in your bank's official app/statement before clicking Approve. Approval grants 30 days and the expiry is enforced by the backend. Direct UPI is one-time and does not auto-renew. The app deliberately does not ingest forwarded bank SMS or auto-approve from SMS content. Do not forward full bank messages because they may expose unrelated account information.


## Backup, recovery and production checks
Configure `BACKUP_ENCRYPTION_KEY` in Render Environment as a random 32-byte hexadecimal secret (64 characters). Do not store it in GitHub or beside exported backups. The Admin page offers a protected **Download encrypted backup** action. Backups are manual encrypted snapshots; download them regularly and retain copies separately. To restore, use the same key and the documented `backend/backup_restore.py restore <backup-file> --confirm-overwrite` command against a staging database first. Restore overwrites the target database.

Set `DATABASE_URL` to a managed PostgreSQL connection for durable production data, or attach a persistent disk and set `FAMILYGUARD_DB_PATH` to its mount path. Render's free ephemeral filesystem is not a durable database. Verify provider-side database backups and perform a restore drill before production launch. The `/health` response reports whether encrypted backup configuration is present and warns when SQLite appears to use a local path.
