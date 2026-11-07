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

Verdict: **NOT PRODUCTION READY**. Nothing is hardware-verified.
