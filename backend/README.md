# FamilyGuard API

FastAPI backend for verified Play subscriptions and opt-in family location sharing.

## Render deployment

Create a Render Web Service from this repository:
- Root directory: `backend`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`

Set these private Render environment variables:

| Variable | Purpose |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Raw Firebase Admin service-account JSON (secret) |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | Raw Google Play Developer API service-account JSON (secret) |
| `PACKAGE_NAME` | `com.princeofworld.familyguard` |
| `PLAY_SUBSCRIPTION_PRODUCT_ID` | `familyguard_premium_999_monthly` |
| `PLAY_SUBSCRIPTION_BASE_PLAN_ID` | `monthly-999` |
| `INVITE_TTL_MINUTES` | Optional; defaults to `15` |

The Firebase project must have Email/Password Authentication enabled and Firestore provisioned. The Firebase Admin service account must belong to that project. The Play service account must have the minimum permission to view subscription data in the app's Play Console. Do not commit either JSON key or place them in the Android app.

## GitHub Actions secrets

Set:
- `FAMILYGUARD_API_BASE_URL`: the Render HTTPS service root (for example `https://your-service.onrender.com`)
- `FIREBASE_WEB_API_KEY`: Firebase project's Web API key (not a service-account secret)
- For signed releases: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`

The Android app uses Firebase Identity Toolkit REST sign-in/sign-up, so no `google-services.json` or Google Services Gradle plugin is required. If the Firebase API key or Render URL is unset, family features show a configuration message and remain unavailable. The app receives only the Web API key; Admin and Play service-account JSON stays on Render.

## API

All endpoints except `/health` require `Authorization: Bearer <Firebase ID token>`.

- `POST /verify-subscription`: verifies `purchases.subscriptionsv2.get` for the configured package/product/base plan and stores the authenticated user's verified Premium expiry in Firestore.
- `POST /family/create`: active verified Premium account creates a family and receives a one-use six-digit invite code (15-minute default expiry).
- `POST /family/join`: another authenticated account redeems a valid code.
- `POST /family/consent`: each member explicitly enables or revokes location sharing. Revocation deletes that member's last location immediately.
- `POST /location/update`: accepts coordinates only from an authenticated member who opted in.
- `GET /location/family?familyId=...`: returns only locations from members who have opted in. Viewing also requires the viewer to have opted in.
- `DELETE /account-data`: deletes this account's family membership, location records and Premium entitlement record. Google Play billing is managed separately.

Location transport must use HTTPS. Firestore provides encryption at rest; this is not end-to-end encryption. Location sharing starts only after a visible user action and displays an ongoing Android foreground-service notification. Members can stop sharing and revoke consent from the app. The app does not offer covert/background monitoring without visible disclosure.

## Operational notes

Free Render instances may sleep or throttle; the app must fail closed when the service is unavailable. Add production rate limiting, abuse monitoring, backups, retention policy and security review before public launch. Six-digit codes should be short-lived and are single-use. Do not log ID tokens, purchase tokens, coordinates or request bodies.
