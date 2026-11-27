# PrinceCircle Privacy Policy

**Effective date:** 27 November 2026

PrinceCircle is a family-safety service built around visible, user-controlled sharing. It is not an emergency-response service. This policy describes the current application and backend behavior.

## Information processed

### Account and family membership
The service stores the email address used for sign-in, a salted scrypt password hash, account/profile fields, device registrations, family membership and invite-code metadata. Signed session tokens are used for authentication. Passwords are not stored in plain text.

### Location and device check-ins
Location is processed only after the device user grants Android foreground location permission, joins a family, enables location consent and starts the visible location-sharing foreground service. The service requests location updates approximately every 60 seconds while active and may continue while the app is minimized. Each session automatically ends after 10 minutes. A persistent Android notification remains visible and includes a Stop control. The service sends latitude, longitude, accuracy, capture time and optional battery percentage over HTTPS for opted-in family members. PrinceCircle does not request `ACCESS_BACKGROUND_LOCATION`. Stopping sharing or revoking consent stops future updates; the server rejects uploads when consent is off and removes the member's current saved location. The dashboard's online indicator is based on a recent check-in and is not a guarantee of connectivity.

### Optional device summaries
When the device user enables the relevant switches, grants the required Android access and the server-side summaries consent is on, the app may upload:
- Installed application display names, package names, app icons and the installed-app count.
- The device's aggregate foreground screen-time total for the current day.
- Today's total call count and total call duration, plus the total contacts count.
- Battery percentage when its separate consent is enabled.

The app does not upload call participants, phone numbers, contact names, contact contents, message contents or browser history. Call and contact summaries require Android Call Log and Contacts permissions. App usage requires the user to enable Android Usage Access in Settings. Summary synchronization is scheduled periodically while enabled and can be stopped by turning the relevant switches off or revoking server consent. The Android app does not request SMS-reading or SMS-sending permissions; it does not read SMS messages.

### User-started camera and screen sharing
Camera or screen sharing starts only after the device user presses the relevant control and accepts an in-app disclosure. Camera sharing additionally requires Android camera permission. Screen sharing additionally requires the Android system's own MediaProjection confirmation for the selected screen. The phone shows an ongoing foreground-service notification with a Stop control for the full session. Video is transmitted through WebRTC to a viewer signed in to the same account. The backend stores short-lived session and signaling records needed to establish the connection and obtains temporary ICE/TURN credentials from the configured Metered provider. The WebRTC signaling session expires after 10 minutes. The backend signaling endpoints do not store the live video stream as a recording. The user can stop sharing at any time; the app does not use the microphone and does not request microphone permission.

### Remote alerts and in-app pause requests
A signed-in account can send an alert, ring-style notification or temporary in-app pause request to a device registered to that same account. The Android app checks for commands while it is open in the foreground; this is not guaranteed background push delivery. Visible notifications require notification permission and notification settings to allow them. A temporary in-app pause is only activated after the device user accepts it on the phone. The user can decline the request or unlock the app locally. This feature does not lock Android, other apps or device settings. Command messages and delivery/acknowledgement status are stored by the backend for a limited command lifetime (30–300 seconds).

### Photos selected by the user
If the user chooses a photo through Android's system photo picker, that selected photo can be uploaded to the account's private gallery. PrinceCircle does not silently browse or upload the photo library. Uploaded gallery media is stored by the service until deleted or the account is deleted.

### Premium payment (direct UPI)
The current direct UPI flow is a one-time ₹999 payment for 30 days of Premium using a UPI app selected by the user. It does not create an automatic recurring mandate. The service stores the submitted 12-digit UTR, account association, amount, submission time, review status, review time and any administrator review note. Submission remains Pending and does not activate Premium. Only an authorized administrator, after verifying the exact credit in the bank's official transaction history, can approve the request and activate Premium for 30 days. Duplicate UTR values are rejected. The service does not collect UPI PINs, OTPs or full bank credentials.

## Android permissions and controls
The Android app declares Internet, foreground location, camera, notification, Usage Access (special Android access), Call Log and Contacts permissions as needed by enabled features. Screen capture uses Android's user-confirmed MediaProjection flow. The app does not declare background-location, microphone, SMS-read or SMS-send permissions. Each feature requires its own user action and relevant permission/consent. No feature is intended for covert monitoring.

## Data retention and deletion
- Location history may remain in the database after it is no longer shown as a recent/online location; revoking consent removes the member's current saved location.
- WebRTC session access expires after 10 minutes. Signaling records are backend session metadata, not stored video recordings.
- Remote commands expire after their configured short lifetime.
- Gallery photos, account/device data, family membership and payment-review records are removed through account deletion where covered by the account's database records. Records independently held by a UPI app or bank remain subject to that provider's retention practices.
- Direct UPI is one-time and does not create a recurring mandate.

## Security and service providers
Production API requests use HTTPS. Passwords are hashed with scrypt and session tokens are signed using a server-side secret. Metered is used to obtain temporary ICE/TURN credentials for WebRTC connectivity. The service is hosted on Render. No security measure guarantees absolute protection; users should protect passwords, invite codes and sensitive location information.

## Children's privacy and appropriate use
A parent or legal guardian should review the in-app disclosure before enabling relevant permissions for a child. The disclosure is not identity or age verification. Use the service only with the knowledge and permission required by applicable law. Do not use PrinceCircle for covert monitoring, harassment or unauthorized tracking.

## Contact
Project support: https://github.com/princeofworld/PrinceHacker/issues

Do not post passwords, session tokens, payment credentials, UPI PINs, OTPs or precise location data in public issues.
