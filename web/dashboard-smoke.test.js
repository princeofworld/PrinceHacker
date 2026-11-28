"use strict";
const fs = require("node:fs");
const assert = require("node:assert/strict");

const html = fs.readFileSync("web/dashboard.html", "utf8");
const homepage = fs.readFileSync("web/index.html", "utf8");
assert.match(homepage, /releases\/latest\/download\/Golden\.Prince-release-signed\.apk/, "Homepage APK download must match the published release asset");
assert.doesNotMatch(homepage, /releases\/latest\/download\/Golden Prince-release-signed\.apk/, "Homepage must not use the old broken APK filename");
assert.match(html, /releases\/latest\/download\/Golden\.Prince-release-signed\.apk/, "Dashboard APK download must match the published release asset");
const requiredTabs = [
  "overview",
  "profile",
  "devices",
  "payments",
  "privacy",
  "location-history",
  "installed-apps",
  "live-sharing",
  "media-gallery",
  "premium",
];

for (const tab of requiredTabs) {
  assert.match(html, new RegExp('data-tab="' + tab + '"'), "Missing dashboard tab: " + tab);
  assert.match(html, new RegExp('id="tab-' + tab + '"'), "Missing dashboard panel: " + tab);
}
for (const script of ["/dashboard.js", "/dashboard-features.js", "/media-gallery.js"]) {
  assert.ok(html.includes('src="' + script), "Missing dashboard script: " + script);
}
assert.doesNotMatch(html, /data-tab="(?:family|sos|sos-alerts)"/i, "Removed Family/SOS tabs must not return");

const js = [
  fs.readFileSync("web/dashboard.js", "utf8"),
  fs.readFileSync("web/dashboard-features.js", "utf8"),
  fs.readFileSync("web/media-gallery.js", "utf8"),
].join("\n");
for (const id of [
  "device-list", "consent-controls", "payment-history", "fg-overview-account",
  "fg-location-content", "fg-app-content", "fg-live-sessions", "fg-live-video",
  "fg-media-content", "fg-refresh-media",
]) {
  assert.ok(html.includes('id="' + id + '"'), "Dashboard HTML is missing required control: " + id);
  assert.ok(js.includes(id), "Dashboard scripts no longer reference required control: " + id);
}
const featureJs = fs.readFileSync("web/dashboard-features.js", "utf8");
assert.ok(featureJs.includes('api("/api/profile")'), "Overview must use the profile endpoint that returns the display name");
const backend = fs.readFileSync("backend/main.py", "utf8");
assert.ok(backend.includes("payload.today_calls_count if payload.calls_contacts_sync_enabled else 0"), "Call totals must be cleared when calls/contacts sync is OFF");
assert.ok(backend.includes("payload.contacts_count if payload.calls_contacts_sync_enabled else 0"), "Contact totals must be cleared when calls/contacts sync is OFF");
process.stdout.write("Golden Prince dashboard smoke checks passed.\n");
