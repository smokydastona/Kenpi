# Android companion

Native Kotlin app in `android/`. It talks to the authenticated Kenpi gateway (HTTPS and
`/ws`), not the loopback API. It is a diagnostic/status companion, not the Kenwood interface
(no DMX5710S display/touch pathway exists).

## Layout

- `android/core` - pure-JVM protocol client (no `android.*`): `Pairing` (strict `kenpi://pair`
  parsing, expiry, IP-literal address), `DeviceKey`/`Proofs` (ECDSA P-256 / SHA-256 proofs
  identical to the server), `PinnedTls` (leaf-certificate SHA-256 pin; no CA trust, no
  hostname fallback), `GatewayApi` (enroll, collect, refresh, snapshot, wireguard; bounded
  bodies), `Session` (serialised refresh, rotation persisted, revocation/replay erases
  credentials), `LiveChannel` (token in first frame, resume by sequence, resync snapshot,
  reconnect with backoff, 4401 -> revoked, 4403 -> remote denied), `Pairer`.
- `android/app` - Keystore P-256 key (`KeystoreDeviceKey`, non-exportable), encrypted
  credential store, QR scan (zxing) with paste fallback, Material 3 (dynamic colour) screens:
  pairing/approval wait, dashboard, camera, vehicle, health, media, remote (WireGuard
  parameters), device, diagnostics. The socket is held only while the activity is visible.
- Camera preview, media browse/download/playback and any control are shown as **unavailable**:
  the A810 Lite protocol is unverified, and the app shows only what the gateway returns.

## Tests

```sh
# core against the real Python gateway (tests/android_interop_server.py)
cd android
KENPI_CORE_ONLY=1 KENPI_INTEROP_PYTHON=python KENPI_INTEROP_ROOT=.. ./gradlew :core:test
# CI also runs :app:testDebugUnitTest :app:assembleDebug
```

Interop covers: pairing with approval and denial, one-time credential collection, pin
mismatch rejected, bad token 401, refresh rotation, refresh replay -> revocation, live event
delivery, and remote revocation closing the stream. `KENPI_CORE_ONLY=1` skips the Android
plugins where Google Maven is unreachable. Without `KENPI_INTEROP_*` the interop tests skip
(reported as skipped, not passed).

## Not verified

No emulator or physical Android device has run the app: Keystore, QR scanning, camera
permission, EncryptedSharedPreferences and the Compose UI are compiled in CI but untested at
runtime. Remote (WireGuard) use from a phone is untested; the app displays tunnel parameters
only and does not configure the VPN.

## Verified build status

Verified on a Linux host (JDK 17, Gradle 8.9, no Android SDK):

```
cd android
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 KENPI_CORE_ONLY=1 \
  KENPI_INTEROP_PYTHON=$(which python) KENPI_INTEROP_ROOT=$(cd .. && pwd) \
  ./gradlew --no-daemon :core:test
```

Result: `:core` (the gateway client, `Api.kt`, pairing, `LocalHelper`) compiled and all 17 tests
passed with 0 skipped, including 7 interop tests that run the Kotlin client against the real
Python gateway. JDK 27 fails with Gradle 8.9; use JDK 17.

Not verified: the `:app` module (Compose UI, `AppModel`, Keystore, `MainActivity`). It needs the
Android Gradle Plugin from Google's Maven repository (`dl.google.com`), which was unreachable
from this host, so the app has not been compiled, instrumented or packaged as an APK.

## CI

`.github/workflows/android.yml` runs on a runner with network access to Google's Maven: unit and
interop tests, lint, debug and release APK builds with SHA-256 checksums (uploaded as artifacts),
and an emulator job that installs the debug APK, launches `MainActivity` and fails on a crash.
The release APK is unsigned (no signing config exists) and is not installable as is. The workflow
has not yet been run; its first run is the first real verification of the `:app` module.

## Voice on the phone

The Assistant tab uses Android's built-in `RecognizerIntent` (Speak button; the
transcript fills the text box and is only sent after you press Send) and
`TextToSpeech` (optional "Read replies aloud"). No extra dependencies or
permissions. **Not compiled or run**: it needs the Android CI job (see CI above).
If the phone has no recognizer or TTS engine the UI says so.

## Camera network probe (capture aid)

The More tab has a "Probe camera network" button (`CameraProbe` in `:core`, unit-tested on
the JVM against a local server; the `AppModel`/UI wiring is uncompiled here). With the phone
joined to the camera's Wi-Fi it connects, over that Wi-Fi network only, to the gateway
address and records which of a fixed list of common TCP ports accept a connection and the
HTTP/banner text they answer. It refuses non-private addresses, sends no credentials or
commands, and redacts cookies, tokens, SSIDs and MAC addresses. It is not a packet
capture and does not make the app able to control a 70mai camera: use PCAPdroid (or a rooted
`tcpdump`) for captures. PCAPdroid is GPL-3.0, so Kenpi neither embeds nor copies it; an
in-app VpnService capture engine is not implemented.
