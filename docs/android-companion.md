# Android companion

The native Kotlin/Jetpack Compose app in `android/` is a read-only client for
Kenpi's existing HTTP status contract:

- `GET /healthz`
- `GET /api/v1/capabilities`
- `GET /api/v1/status`

It polls these endpoints while monitoring is enabled, displays only the returned
health and declared capability data, and accepts HTTPS base URLs. It does not
send camera commands. Kenpi currently has no WebSocket endpoint, so the app
reports that real-time updates are unavailable and uses bounded HTTP polling.

This is an optional **diagnostic companion**, not the primary Kenwood user
interface. Its launcher activity is not an Android Auto application, and a
head unit supporting Android Auto does not make an arbitrary Compose activity
projectable. No working DMX5710S display/touch pathway is implemented.

Endpoint changes clear the snapshot and timestamp. Failed refreshes clear old
data; response revision checks discard obsolete results even if an endpoint
changes from A to B and back to A. Monitoring stops when the activity stops.

## Build and test

Requires Android SDK platform 35, Java 17, and the Android SDK build tools.
The wrapper pins Gradle 8.9 with a distribution SHA-256; build plugins pin AGP
8.7.2 and Kotlin/Compose compiler 2.1.20. Compose BOM 2025.04.01 and
activity-compose 1.10.1 are separately pinned because the Compose BOM does not
manage Activity versions.

```sh
cd android
./gradlew testDebugUnitTest assembleDebug
```

In the implementation environment, Google Maven's `dl.google.com` hostname did
not resolve. Full Android artifact resolution, APK compilation, and Android
unit-test task execution are therefore **not verified**. Isolated JVM validation
of URL and store logic passed 20 JUnit tests; it is not a substitute for the
commands above or an on-device test. No Android CI success is claimed.

## Connectivity and security boundary

The current Kenpi API binds to loopback and has no TLS or authentication. An
Android device on vehicle Wi-Fi cannot connect directly to that API, and the
companion app intentionally rejects cleartext HTTP. To use the app remotely,
deploy a TLS gateway reachable only through an access-controlled private
network or VPN, and expose only the read-only status routes. The app does not
implement client credentials. Do not change Kenpi's loopback binding or expose
unauthenticated status/API routes to a network to make the app connect.

The default endpoint is blank rather than an assumed camera or Raspberry Pi
address. The endpoint must be the HTTPS URL of that separately secured gateway.
No WebSocket, command, camera-control, or live-preview behavior is claimed.
