# FamilyGuard Privacy Policy

**Effective date:** 21 November 2026

FamilyGuard is a family-safety dashboard for Android. This policy describes the current app implementation in this repository. It is not a promise that a future version will behave identically; material changes will be reflected here and in the app's disclosure.

## 1. Information and permissions

### Location — foreground only
The current Android manifest requests `ACCESS_FINE_LOCATION` and `ACCESS_COARSE_LOCATION`. If the device user chooses the location feature, Android displays its permission prompt. FamilyGuard obtains a current or last-known location only after that user action and displays the result on the device.

**Background location:** The current release does not request `ACCESS_BACKGROUND_LOCATION`, does not run a background location service, and does not continuously track location. If a future version adds background location, it must be separately disclosed here and in a prominent in-app notice before the permission is requested.

### App usage — Usage Access
The app declares `PACKAGE_USAGE_STATS`. This is a special Android access, not an ordinary runtime permission. A parent/guardian or device user must choose the usage-report feature and explicitly enable FamilyGuard in Android's Usage Access settings. The app reads recent app-usage totals to display a report on the device. Usage reports are not uploaded to a FamilyGuard server by this version.

### Other permissions
The app may request notification permission on supported Android versions for user-visible notifications. The app does not request microphone, camera, call-log, contacts, SMS-read, or screen-capture permissions in the current manifest.

## 2. Parental consent and transparency

Before the app can request foreground location permission or open Usage Access settings, it displays an in-app prominent privacy disclosure. An adult parent/legal guardian must affirm that they have read the notice and consent to enabling the selected features. The acceptance choice is stored locally on the device.

This confirmation is an on-device declaration only; it does not verify a person's identity, age, or legal authority. The parent/guardian should explain the enabled features to the device user and obtain any consent required by applicable law. FamilyGuard must not be used for covert monitoring. The device user can review and revoke Android permissions in Settings.

## 3. How information is used and shared

- Location is used to provide the user-requested location display.
- App-usage information is used to display the local usage report.
- The parental disclosure acceptance flag is stored in the app's local preferences.
- The verification backend receives the Google Play purchase token only when checking a subscription. It calls Google Play Developer API and does not persist the token or location/usage reports. The current Android app does not upload location or app-usage reports to the verification backend.
- Google Play handles subscription checkout and payment processing. FamilyGuard receives purchase status and purchase-token information from the Google Play Billing Library to determine whether the in-app subscription is available. Payment-card and bank details are handled by Google Play, not by FamilyGuard.

No advertising or analytics SDK is included in the current app module dependencies. Re-check this statement if dependencies or app behavior change.

## 4. Subscription and billing

The app integrates Google Play Billing Library 8 for the subscription product ID `familyguard_premium_999_monthly` and sends the purchase token to the configured HTTPS verification backend. Premium remains locked if the backend URL is not configured or Google Play cannot confirm an active subscription. The intended price shown in the app is ₹999 per month. Actual availability, price, tax, renewal, cancellation, eligibility, and billing terms are determined by the offer configured in Google Play Console and shown in Google Play's checkout UI. A source-code product ID or displayed price alone does not establish that a Play Console product is active.

## 5. Data retention and deletion

Location and usage report values are displayed in memory and are not stored in a FamilyGuard cloud account by this version. The parental-disclosure acceptance flag remains in local app preferences until app data is cleared or the app is uninstalled. Google Play retains billing records under Google's policies. To clear local FamilyGuard preferences, clear the app's storage or uninstall the app; subscription cancellation must be handled through Google Play.

## 6. Data security

Android permission controls and Google Play Billing are used for the functions described above. The optional backend includes a Firebase-authenticated account-data deletion route, but this Android app does not yet include Firebase sign-in or a connected FamilyGuard cloud account. Do not treat cloud account deletion as available until that integration is completed and deployed. No app can guarantee absolute security; keep the device updated and protect access to it.

## 7. Children's privacy

FamilyGuard is designed for transparent, parent-supervised use. A parent/legal guardian should review this policy and the in-app notice before enabling any optional access. Do not enable monitoring without the knowledge of the device user or any consent required by local law. The app's on-device parent confirmation is not age verification or a substitute for legally required parental-consent processes.

## 8. Your choices

You can decline the in-app notice and exit the app. You can revoke location permission and Usage Access at any time in Android Settings. You can stop using the app by uninstalling it. You can manage or cancel a subscription in Google Play.

## 9. Changes to this policy

This policy should be updated whenever the app adds data collection, background access, cloud transmission, analytics, advertising, new permissions, or new third-party SDKs. The effective date above will be changed when the policy is revised.

## 10. Developer contact

For privacy questions or requests, use the repository's contact channel:

- **Project:** FamilyGuard
- **Repository:** https://github.com/princeofworld/PrinceHacker
- **Contact:** https://github.com/princeofworld/PrinceHacker/issues

Please do not post location history, usage reports, passwords, purchase tokens, or other sensitive personal information in a public issue. A public residential address and personal phone number are intentionally not published in this policy.
