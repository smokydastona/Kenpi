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
| Pairing, per-device credentials, revocation (Pi side) | IMPLEMENTED, tested; Android client NOT IMPLEMENTED (ADR 0002) |
| Authenticated remote gateway (WireGuard) | IMPLEMENTED policy + config rendering; live tunnel UNVERIFIED (ADR 0003) |
| Backup/restore | IMPLEMENTED (local USB tested; SFTP UNVERIFIED live); scheduled via `kenpi-backup.timer` (ADR 0004) |
| Kenwood DMX5710S display/touch | BLOCKED, unverified |

Verdict: **NOT PRODUCTION READY**. Nothing is hardware-verified.
