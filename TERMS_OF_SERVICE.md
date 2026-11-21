# FamilyGuard Terms of Service

**Effective date:** 21 November 2026

## 1. Scope
FamilyGuard is an informational family-safety utility. It is not an emergency-response, medical or law-enforcement service.

## 2. Consent and lawful use
Use family and location features only with the knowledge and explicit permission of each device user and any additional consent required by applicable law. Every family member must independently opt in to location sharing. Do not use the app for covert surveillance, harassment or unauthorized monitoring. Users can stop sharing and revoke consent at any time.

## 3. Family location sharing
A Premium account with a verified active subscription can create a family and generate a one-use six-digit invite code. Codes expire after 15 minutes. Another Firebase-authenticated user can join with the code. Location is sent only after the member grants Android foreground location permission, accepts the in-app consent and starts the visible location foreground service. An ongoing notification indicates sharing is active. Only opted-in members' recent locations are returned. Results older than 10 minutes are not returned. Sharing depends on Firebase, Firestore, Render hosting, Android permissions and network availability.

## 4. Other features and limitations
The app includes a user-reviewed SOS message handoff to the device messaging app; no message is sent automatically. Premium usage summaries require separate Android Usage Access approval and are calculated locally. Location estimates may be inaccurate. The app is not a substitute for direct contact with emergency services.

## 5. Subscription
The intended product ID is `familyguard_premium_999_monthly` with base plan `monthly-999`. The ₹999/month amount is subject to the active Play Console offer and the price shown by Google Play at checkout. Google Play manages billing, renewal and cancellation. Premium and family creation remain unavailable unless the backend verifies an active subscription with Google Play.

## 6. Data and deletion
Firebase Authentication and Firestore store account, family membership, consent and opted-in location data. Revoking consent removes the member's last stored location. The authenticated backend data-deletion endpoint removes FamilyGuard family membership, location and entitlement data, but does not itself delete the Firebase identity or cancel Google Play billing. See the [Privacy Policy](https://github.com/princeofworld/PrinceHacker/blob/main/PRIVACY_POLICY.md).

## 7. Availability
Free Render hosting may sleep or throttle. Family and subscription features may be temporarily unavailable. If authentication or verification fails, the app denies access rather than assuming success.

## 8. Contact
For questions, use the [GitHub issue page](https://github.com/princeofworld/PrinceHacker/issues). Never post credentials, tokens, location details or sensitive personal data publicly.
