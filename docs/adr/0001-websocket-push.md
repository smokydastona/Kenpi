# ADR 0001 — Authenticated WebSocket push

Status: Accepted (design). Implementation: server side implemented and tested (`kenpi/gateway/`); not validated on a Pi or with a real Android client. Camera/video/media events have no producers yet and none are fabricated.

## Decision
REST remains authoritative for snapshots and commands. WebSocket is a
server-to-client synchronisation channel only, not a second command API.
The internal dispatcher (`kenpi/core/events.py`) feeds it; the existing
polling `/api/v1/events` stays as the loopback diagnostic view, not the
primary update path.

## Envelope (schema_version 1)
```json
{"event_id": "uuid", "sequence": 12345, "timestamp": "ISO-8601",
 "type": "camera.recording.status", "schema_version": 1,
 "source": "camera", "payload": {}}
```
The current bus uses `seq`/`id`/numeric `timestamp`/`data`; the WebSocket
layer must map to this envelope (versioned), not change the internal type.
Clients ignore unknown types.

## Required behaviour
- Authenticated connections only (credential sent in the first message or an
  `Authorization` header, never a URL query parameter); TLS off-loopback.
- Initial state snapshot on connect.
- Reconnect with last-seen sequence. If the ring cannot satisfy it
  (`gap`), send a fresh snapshot instead of assuming completeness.
- Bounded outbound queues with explicit backpressure (drop → resync), heartbeats,
  idle timeouts, connection limits, cleanup on disconnect.
- Events filtered by the device's authorised scopes.
- No events are invented when hardware state is unknown.

## Acceptance
Auth failure, expired token, gap/resync, slow consumer, connection-limit and
disconnect-cleanup tests over a real socket.
