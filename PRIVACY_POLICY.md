# FamilyGuard Privacy Policy

**Effective date:** 21 November 2026

FamilyGuard is an opt-in family-safety Android app. This policy describes the current implementation in this repository.

## Information collected and used

### Account and family membership
The app uses Firebase Authentication Email/Password sign-in. Firebase supplies an account ID and ID token. The backend stores family membership, role, invite-code metadata and consent state in Firestore.

### Location
Location is collected only after the device user grants Android foreground location permission, signs in, joins a family, explicitly enables location consent and presses **Start live sharing**. The app then starts a visible Android location foreground service with an ongoing notification and sends periodic coordinates, accuracy and capture time to the FamilyGuard backend over HTTPS. Family members can retrieve only locations from members who have opted in. The viewer must also have opted in. The app does not request `ACCESS_BACKGROUND_LOCATION`; location updates run only while the user-started location foreground service is active. The user can stop sharing and revoke consent in the app; revocation removes that member's last stored location.

The backend returns only locations updated within the last 10 minutes. Firestore encrypts stored data at rest and HTTPS protects data in transit. This is not end-to-end encryption. Location is sensitive personal information; share only with people you trust.

### Subscription
Google Play processes subscription payments. When checking Premium, the app sends a purchase token to the authenticated FamilyGuard backend. The backend calls Google Play Developer API and stores the verified Premium status and expiry for that Firebase account. The raw purchase token is not stored; a one-way hash is used to prevent linking the same purchase token to multiple FamilyGuard accounts.

### App usage
The optional Premium Usage Access report reads app usage on this device after the user enables the special Android Usage Access setting. The report is calculated and displayed locally and is not uploaded to the FamilyGuard backend.

### Other permissions
The app requests notification permission for the persistent sharing notification and other visible notices. The current manifest does not request microphone, camera, call-log, contacts, SMS-read or screen-capture permissions.

## Consent and user control

A parent/legal guardian should review the app disclosure and obtain any consent required by applicable law. Every family member must separately opt in to location sharing. No member can silently enable another member's sharing. The user can revoke sharing at any time; Android permissions can also be revoked in Settings. The ongoing notification indicates when location sharing is active.

## Data retention and deletion

- A family member's last location is removed when that member revokes location consent or deletes their FamilyGuard account.
- Location results older than 10 minutes are not returned by the API.
- Family membership and verified Premium entitlement records are removed by the authenticated `DELETE /account-data` backend endpoint. Google Play billing records are managed separately through Google.
- Family group records may remain if other members still belong to the group. Invite codes expire after 15 minutes and can be redeemed once.
- App-usage reports are calculated locally and are not stored in a FamilyGuard cloud account.
- Firebase Authentication account deletion must be performed through Firebase's account-management process; the backend data-deletion endpoint removes FamilyGuard data but does not itself delete the Firebase identity or cancel Google Play billing.

## Security and third parties

Firebase Authentication and Firestore are used for account, consent and family data. Google Play Developer API is used for subscription verification. Firebase Admin and Google Play service-account JSON keys are stored only in private Render environment variables and must never be included in the app or repository. The app receives only the Firebase Web API key, which is not a service-account credential. Free hosting may sleep or throttle, so sharing and Premium verification may be unavailable temporarily.

## Children's privacy and appropriate use

FamilyGuard is intended for transparent, consent-based family use. Do not use it for covert monitoring, harassment or unauthorized tracking. The app's parent/guardian confirmation is not identity or age verification and is not a substitute for legally required consent.

## Choices

You can decline optional sharing, stop sharing, revoke consent, revoke Android permissions, sign out by clearing app data, and manage subscriptions through Google Play. Location sharing is unavailable if Firebase or the backend is not configured.

## Contact

- Project: FamilyGuard
- Repository: https://github.com/princeofworld/PrinceHacker
- Contact: https://github.com/princeofworld/PrinceHacker/issues

Do not post passwords, ID tokens, purchase tokens, coordinates or other sensitive data in public issues.
