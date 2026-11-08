# Implementation status

Labels: IMPLEMENTED (code + tests), INTEGRATED (wired into `main`),
HARDWARE-VERIFIED (never reached, nothing is above E0), BLOCKED (needs evidence).

| Capability | Status |
| --- | --- |
| Event bus, replay route | IMPLEMENTED, INTEGRATED (coordinator, vehicle link, safe mode) |
| Safe mode | IMPLEMENTED, INTEGRATED |
| Vehicle framing/link | IMPLEMENTED, INTEGRATED; hardware BLOCKED (ESP32 revision and pins) |
| Local media library and routes | IMPLEMENTED, INTEGRATED |
| Head-unit registry | IMPLEMENTED, INTEGRATED |
| Release bundle, OS image | Built in CI; not booted on hardware |
| Camera transport, session, commands | Safety gate only; BLOCKED (no A810 Lite evidence) |
| Live preview | Buffer only; BLOCKED |
| WebSocket push | IMPLEMENTED server-side, unit/integration tested; not field-verified (ADR 0001) |
| Pairing, per-device credentials, revocation (Pi side) | IMPLEMENTED; Python and Android `:core` INTEGRATION_VERIFIED against each other in CI (ADR 0002) |
| Authenticated remote gateway (WireGuard) | INTEGRATION_VERIFIED in CI (userspace wireguard-go, network namespaces; no kernel module, no phone, no WAN) (ADR 0003) |
| Backup/restore | INTEGRATION_VERIFIED (local USB; SFTP against a real OpenSSH server on loopback only); scheduled via `kenpi-backup.timer` (ADR 0004) |
| Android companion app (Keystore key, QR/paste pairing, pinned TLS, token refresh, WebSocket resume, Compose UI) | IMPLEMENTED; `:core` INTEGRATION_VERIFIED against the real gateway; app UI compiles in CI but has no emulator/device run, so not HARDWARE_VERIFIED |
| Kenwood DMX5710S display/touch | BLOCKED, unverified |

## Register (generated from `validation/register.toml`)

<!-- register:begin -->
| Capability | Implementation | Hardware | Evidence | Missing |
| --- | --- | --- | --- | --- |
| camera_control | blocked | yes | E0 | A810 Lite transport, pairing/auth, and command evidence (E3 capture) |
| live_preview | blocked | yes | E0 | Observed A810 Lite stream URL, codec and auth; buffer and metrics are implemented |
| media_access | partial | yes | E0 | Camera file listing/download protocol; local library is implemented |
| kenwood_touchscreen | blocked | yes | E0 | Physical DMX5710S display and touch-return validation |
| vehicle_power_coordination | partial | yes | E0 | Verified ESP32-S3 board revision, pinout and bench electrical test |
| head_unit_registry | complete | no | E0 | - |
| read_only_api | complete | no | E0 | - |
| research_corpus | complete | no | E0 | - |
<!-- register:end -->

Verdict: **NOT PRODUCTION READY**. Nothing is hardware-verified.
