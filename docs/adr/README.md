# Architecture decision records

| ADR | Decision | Implementation |
| --- | --- | --- |
| [0001](0001-websocket-push.md) | Authenticated WebSocket event channel | NOT IMPLEMENTED |
| [0002](0002-android-pairing.md) | QR enrolment, per-device revocable credentials | NOT IMPLEMENTED |
| [0003](0003-remote-gateway.md) | Local-first, WireGuard remote access | NOT IMPLEMENTED |
| [0004](0004-backup-restore.md) | Versioned backups, transactional restore | NOT IMPLEMENTED |

Status is "Accepted" for the design only. A record's feature is complete only
with integration evidence and failure-path tests, not interfaces or mocks.

Shared rules (mandatory for all four):

1. Kenpi Core is the sole owner of state. REST, WebSocket, Android, remote
   access and backup/restore act only through explicit contracts.
2. No permanent shared Android credential; no credentials in the APK.
3. No public API exposure by default; VPN is never a substitute for auth.
4. No WebSocket tokens in URL query parameters.
5. No silent loss of event history, backups or user media.
6. No restoring revoked credentials; no destructive restore without a recoverable pre-restore state.
7. No fabricated camera state or hardware success.
8. The unauthenticated loopback API stays loopback-only and read-only.
9. Implementations are independent (MIT); OctoPrint/OctoPi/AppCompanion are concept references only.

Order: event dispatcher (done: `kenpi/core/events.py`) → WebSocket + enrolment →
authorization → remote gateway → backup → restore → Android → recovery automation.
