# FamilyGuard Privacy Policy

**Effective date:** 22 November 2026

FamilyGuard is a transparent, opt-in family-safety service. This policy describes the current repository implementation. The service is not an emergency-response service.

## Information processed

### Account and family membership
FamilyGuard stores the email address used for sign-in and a salted scrypt password hash. It issues a signed session token. Family group membership, invite-code metadata and each member's location-consent state are stored in the backend database (SQLite or PostgreSQL, depending on deployment configuration). Passwords are not stored in plain text.

### Location and device check-ins
Location is processed only after the device user grants Android foreground location permission, joins a family, explicitly enables location consent and starts the visible location-sharing foreground service while the app is in use. While the service is active, it requests location updates approximately every 60 seconds (subject to Android location availability and movement) and may continue while the app is minimized. A persistent Android notification remains visible while sharing is active. The service sends coordinates, accuracy, capture time and an optional battery percentage to the backend over HTTPS for the opted-in family. FamilyGuard does not request `ACCESS_BACKGROUND_LOCATION`; location sharing runs through the user-started foreground service and stops when the user stops sharing or revokes consent. Revocation removes that member's last saved location. The API returns only records from members who have individually opted in and whose last check-in is no older than ten minutes. The dashboard's "online" indicator is an estimate based on a recent check-in, not a guarantee of network connectivity.

### Premium payment (direct UPI)
The current payment flow is a one-time ₹999 UPI payment for 30 days of Premium using a UPI app selected by the user. It does not create an automatic recurring mandate. FamilyGuard stores the submitted 12-digit UTR, account association, amount, submission time, review status, review time and any administrator review note. A UTR submission remains Pending and does not activate Premium. Only an authorized administrator, after checking the exact ₹999 credit in the bank's official transaction history, can approve the request and activate Premium for 30 days. Duplicate UTR values are rejected. The UTR is visible only to the account holder and authorized administrators. FamilyGuard does not collect or store UPI PINs, OTPs, full bank credentials or full bank statements. Do not send bank SMS messages, unrelated transaction details, OTPs or account credentials to support.

### App usage
The optional Premium usage summary reads app names and foreground-use durations for the last 24 hours locally on the Android device only after the user separately enables Android Usage Access in Settings. The summary is calculated on-device and is not uploaded by this implementation.

### Android permissions and device access
The Android app declares Internet access; foreground location (`ACCESS_FINE_LOCATION` and `ACCESS_COARSE_LOCATION`); location foreground-service permissions; and notification permission on Android versions that require it. Location sharing is started by the device user and uses a persistent notification. The app does not declare `ACCESS_BACKGROUND_LOCATION`, microphone, camera, call-log, contacts, SMS-read, SMS-send or screen-capture permissions. The SOS feature opens the user's SMS app with a draft; the user reviews and sends it. Optional Premium App Usage & Screen Time uses `PACKAGE_USAGE_STATS`, a special access that the user must separately enable in Android Settings. It reads app names and foreground-use durations for a recent 24-hour summary on the device only; this usage summary is not uploaded to FamilyGuard servers. Usage Access can be revoked in Android Settings. The app does not provide covert monitoring.

## Consent and user control
Every participating device user must separately opt in to location sharing. No family member can silently enable another member's sharing. Users can stop sharing, revoke Android permissions or sign out. Parent/legal-guardian confirmation in the app is an on-device disclosure and is not identity or age verification.

## Data retention and deletion
- Location records older than ten minutes are not returned by the family-location API; they may remain in the database until replaced or deleted.
- Revoking consent deletes that member's current location record.
- Users can delete their account in Android Settings within FamilyGuard or from the signed-in website dashboard. Account deletion removes the account, device registrations, UTR review records and memberships; an owned family is transferred to a remaining member or deleted if empty.
- Invite codes expire after fifteen minutes and can be redeemed once.
- Locally calculated app-usage summaries are not uploaded to the service.
- UPI UTR submissions and review records are retained with the account until account deletion or required fraud-prevention retention expires. Direct UPI is a one-time payment and does not create a recurring mandate. Direct UPI payments do not create recurring mandates. Records independently held by a UPI app or bank remain subject to that provider's retention practices.

## Security and service providers
Requests use HTTPS in production. Passwords are hashed using scrypt and session tokens are signed by a server-side secret. Direct UPI payments are manually reviewed by an authorized administrator. The service is hosted on Render. No security measure guarantees absolute protection; avoid sharing passwords, invite codes or sensitive location details.

## Children's privacy and appropriate use
FamilyGuard is designed for transparent, consent-based family use. Do not use it for covert monitoring, harassment or unauthorized tracking. Obtain any consent required by applicable law before sharing another person's information.

## Contact
Project support: https://github.com/princeofworld/PrinceHacker/issues

Do not post passwords, session tokens, payment credentials, UPI PINs, OTPs or precise location data in public issues.
