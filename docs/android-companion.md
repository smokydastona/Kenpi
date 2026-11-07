# Android companion

The native Kotlin/Jetpack Compose app in `android/` is a read-only client for
Kenpi's existing HTTP status contract:

- `GET /healthz`
- `GET /api/v1/capabilities`

It polls these endpoints while monitoring is enabled, displays only the returned
health and declared capability data, and accepts HTTPS base URLs. It does not
send camera commands. Kenpi currently has no WebSocket endpoint, so the app
reports that real-time updates are unavailable and uses bounded HTTP polling.

## Build and test

Requires Android SDK platform 37, Java 17, and the Android SDK build tools.

```sh
cd android
./gradlew testDebugUnitTest assembleDebug
```

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
