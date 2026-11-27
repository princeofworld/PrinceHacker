# PrinceCircle

A consent-first family-safety Android app with a companion website dashboard and FastAPI backend.

## Current implemented behavior

- Android app: Kotlin + Jetpack Compose, Gradle Kotlin DSL, JDK 17; current app version 1.3.4 (versionCode 9).
- Location sharing: the participating device user signs in, joins a family, accepts the disclosure, grants foreground location permission and explicitly starts sharing. A visible foreground-service notification includes Stop. Sessions auto-stop after 10 minutes; no background-location permission is requested.
- Device summaries: separate consent and Android permissions control optional installed-app names/packages/icons, aggregate screen time, call count/duration, contacts count and battery percentage. SMS messages and private communications are not read.
- Live media: camera and screen sharing are user-started, preceded by an in-app disclosure and Android permission/system confirmation, and show a persistent notification with Stop. WebRTC uses temporary Metered ICE/TURN credentials and short-lived signaling sessions. The signaling API does not record the video stream.
- Remote safety commands: dashboard alerts/ring-style notifications are checked while the app is foregrounded. Temporary in-app pause requests require acceptance on the device and can be declined/unlocked locally; Android and other apps are never locked.
- Private photo gallery: users explicitly choose photos through Android's system photo picker before upload.
- Payments: one-time ₹999 UPI for 30 days, with manual UTR verification; no automatic recurring mandate.
- Backend: FastAPI with SQLite fallback and PostgreSQL support through DATABASE_URL.
- GitHub Actions: backend regression tests (SQLite/PostgreSQL), frontend JavaScript syntax checks, Android APK build, APK signature verification and artifact upload. If release signing secrets are absent, the workflow produces an unsigned debug APK artifact, not a signed release.

## Build

GitHub Actions uses Gradle 8.13, JDK 17 and Android SDK 36. It builds a signed release when all signing secrets are configured; otherwise it builds a debug fallback.

## Important

PrinceCircle is for transparent, consent-based use only. Every participating device user controls their own sharing. Automated tests and APK builds do not replace real-device testing or legal/privacy review. The service is not an emergency-response service.
