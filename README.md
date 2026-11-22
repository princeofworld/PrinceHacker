# FamilyGuard

A consent-first family safety and parental controls Android app with a companion website and API.

## Current implemented behavior

- Android app: Kotlin + Jetpack Compose, Gradle Kotlin DSL, JDK 17.
- Location sharing: the participating device user must sign in, join a family, accept the in-app disclosure, grant foreground location permission, and explicitly start sharing. Sharing runs in a visible location foreground service with an ongoing notification; no background-location permission is requested.
- Session limit: each sharing session has a fixed 10-minute auto-stop. The service removes location updates, revokes local consent, requests server-side consent revocation, records the session as `expired`, and shows a stopped notification. A new session requires a fresh user Start action.
- Privacy disclosure: the app explains the location fields collected, family recipients, visible notification, session duration, and stop/revoke controls before it requests location permission.
- Privacy and terms: `PRIVACY_POLICY.md` and `TERMS_OF_SERVICE.md` are served by the API and linked from the website and Android app.
- Account deletion: signed-in users can request deletion from the website dashboard or Android app. The API removes the account and associated records and transfers an owned family to a remaining member, or deletes an empty family.
- Backend: FastAPI with SQLite fallback and PostgreSQL support through `DATABASE_URL`.
- GitHub Actions: pushes to `main` run backend regression tests (SQLite and PostgreSQL when configured), frontend JavaScript syntax checks, Android APK build, APK signature verification, and artifact upload. If release signing secrets are absent, the workflow produces an unsigned debug APK artifact; that is not a Play Store release.

## Build

The GitHub Actions workflow uses Gradle 8.13, JDK 17, Android SDK 36, and runs `:app:assembleDebug` or `:app:assembleRelease` depending on configured signing secrets.

## Important

FamilyGuard is intended for transparent, consensual use only. Every participating device user controls their own location sharing. The app is not an emergency-response service. Passing automated tests or building an APK does not by itself certify Google Play policy compliance; verify the final app behavior, disclosures, Data safety form, privacy policy, and account-deletion flow before publishing.
