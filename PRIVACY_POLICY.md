# FamilyGuard Privacy Policy

**Effective date:** 22 November 2026

FamilyGuard is a transparent, opt-in family-safety service. This policy describes the current repository implementation. The service is not an emergency-response service.

## Information processed

### Account and family membership
FamilyGuard stores the email address used for sign-in and a salted scrypt password hash. It issues a signed session token. Family group membership, invite-code metadata and each member's location-consent state are stored in the backend SQLite database. Passwords are not stored in plain text.

### Location and device check-ins
Location is processed only after the device user grants Android foreground location permission, joins a family, explicitly enables location consent and starts the visible foreground location-sharing service. The app displays an ongoing Android notification while sharing is active. The service sends coordinates, accuracy, capture time and an optional battery percentage to the backend over HTTPS. The user can stop sharing and revoke consent at any time; revocation removes that member's last saved location. The API returns only records from members who have individually opted in and whose last check-in is no older than ten minutes. The dashboard's "online" indicator is an estimate based on a check-in in the previous three minutes, not a guarantee of network connectivity.

### Premium subscription
Where configured, Razorpay handles recurring subscription checkout and payment processing. FamilyGuard stores the subscription identifier, status and account association needed to verify entitlements. Full card details, UPI PINs and payment credentials are not collected or stored by FamilyGuard. Razorpay API keys and webhook secrets must remain private server-side environment variables. Premium is enabled only after the backend verifies the payment signature and subscription status or receives a valid Razorpay webhook.

### App usage
The optional Premium usage summary reads app usage locally on the Android device only after the user separately enables Android Usage Access. The summary is calculated on-device and is not uploaded by this implementation.

### Permissions
The Android app uses internet, foreground location and notification permissions for the disclosed features. It does not include microphone, camera, call-log, contacts, SMS-read or screen-capture permissions. The app does not provide covert monitoring.

## Consent and user control
Every participating device user must separately opt in to location sharing. No family member can silently enable another member's sharing. Users can stop sharing, revoke Android permissions or sign out. Parent/legal-guardian confirmation in the app is an on-device disclosure and is not identity or age verification.

## Data retention and deletion
- Location records older than ten minutes are not returned by the family-location API; they may remain in the database until replaced or deleted.
- Revoking consent deletes that member's current location record.
- The authenticated account-data deletion endpoint deletes the account and related records covered by database foreign-key relationships. Some orphaned family metadata may require administrative cleanup.
- Invite codes expire after fifteen minutes and can be redeemed once.
- Locally calculated app-usage summaries are not uploaded to the service.
- Payment-provider records and recurring mandates are managed by Razorpay and are not deleted by the FamilyGuard account-data endpoint. Subscription cancellation is a separate action.

## Security and service providers
Requests use HTTPS in production. Passwords are hashed using scrypt and session tokens are signed by a server-side secret. Razorpay processes payments when enabled. The service is hosted on Render. No security measure guarantees absolute protection; avoid sharing passwords, invite codes or sensitive location details.

## Children's privacy and appropriate use
FamilyGuard is designed for transparent, consent-based family use. Do not use it for covert monitoring, harassment or unauthorized tracking. Obtain any consent required by applicable law before sharing another person's information.

## Contact
Project support: https://github.com/princeofworld/PrinceHacker/issues

Do not post passwords, session tokens, payment credentials, UPI PINs, OTPs or precise location data in public issues.
