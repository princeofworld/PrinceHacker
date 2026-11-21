# FamilyGuard verification backend

## Deploy on Render (free web service)

1. Create a Google Cloud service account dedicated to Play Developer API verification.
2. Enable the Google Play Android Developer API and grant the service account the minimum Play Console permission needed to view subscription/order data for this app.
3. In Render, create a Web Service from this repository and set **Root Directory** to `backend`, build command `pip install -r requirements.txt`, and start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.
4. Set `PLAY_PACKAGE_NAME`, `PLAY_SUBSCRIPTION_PRODUCT_ID`, `PLAY_SUBSCRIPTION_BASE_PLAN_ID`, and `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64` as private environment variables. The last value is the base64 encoding of the service-account JSON. Never commit the JSON or paste it into an issue/chat.
5. Check `/health`. The verification endpoint is `POST /verify-subscription`.

## Verify request

```json
{
  "packageName": "com.princeofworld.familyguard",
  "productId": "familyguard_premium_999_monthly",
  "basePlanId": "monthly-999",
  "purchaseToken": "PLAY_PURCHASE_TOKEN"
}
```

The API calls Google Play Developer API `purchases.subscriptionsv2.get` and only returns `active: true` when the returned subscription is ACTIVE, has a matching product and base plan, and has a future expiry time. Tokens are not logged or stored. The endpoint is intentionally not a substitute for Android's purchase acknowledgement; the app must acknowledge purchases through Play Billing.

## Account data deletion

`DELETE /account-data` requires a Firebase Authentication ID token in the Authorization header and Firebase Admin credentials on the server. It deletes FamilyGuard-owned Firestore documents from `familyguard_user_data` and the account document from `familyguard_accounts`. It does not cancel or delete Google Play billing records. Do not advertise cloud account deletion until Firebase Authentication and Firestore have been configured and the Android sign-in flow has been integrated and tested.

## Important production setup

- Keep service-account JSON only in Render secret environment variables.
- Use HTTPS only. Do not log request bodies, purchase tokens, Authorization headers, or Google API URLs containing tokens.
- Restrict service-account permissions to the one Play app.
- Add production rate limiting and monitoring at the hosting edge before public launch.
- Free hosting can sleep or throttle; subscription checks should fail closed (no Premium) if verification is unavailable.
- The Android app does not yet call this API. Configure `BuildConfig.FAMILYGUARD_VERIFY_URL` and add an authenticated HTTPS client before enabling server-authoritative Premium.
