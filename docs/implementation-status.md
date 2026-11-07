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
| WebSocket push | NOT IMPLEMENTED |
| Android pairing, per-device credentials | NOT IMPLEMENTED |
| Authenticated remote gateway | NOT IMPLEMENTED |
| Backup/restore | NOT IMPLEMENTED |
| Kenwood DMX5710S display/touch | BLOCKED, unverified |

Verdict: **NOT PRODUCTION READY**. Nothing is hardware-verified.
