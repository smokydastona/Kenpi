# Reference-to-Kenpi implementation matrix

Concepts only; no code was copied. OctoPrint (AGPL-3.0), OctoPi (GPL-3.0) and
OctoPrint-AppCompanion (AGPLv3 header) are reference-only for an MIT project.
Biohub/octopi is an unrelated cryo-ET project and is excluded.

| Reference subsystem | Concept | Kenpi destination | Actual implementation | Tests | Licence decision |
| --- | --- | --- | --- | --- | --- |
| OctoPrint event manager | Typed events, central dispatch | `kenpi/core/events.py` | Implemented: sequence numbers, UUIDs, bounded replay ring, drop-oldest queues, isolated callbacks | `tests/test_events.py` | Independent reimplementation |
| OctoPrint push API | Live client updates | `GET /api/v1/events` (polling replay) | Polling only. WebSocket NOT implemented | `tests/test_events.py` | Independent |
| OctoPrint safe mode | Start without extensions | `kenpi/core/safemode.py` | Implemented, one-shot marker file | `tests/test_safemode.py` | Independent |
| OctoPrint file manager | Media catalogue | `kenpi/media/library.py` | Local library only; no camera download | `tests/test_media_library.py` | Independent |
| OctoPrint health checks | Diagnostics | `/api/v1/diagnostics`, `services/health.py` | Host health only | `tests/test_api_routes.py` | Independent |
| OctoPi systemd supervision | Supervised services | `deploy/`, `os-image/` | Units and pi-gen image built in CI; never booted on a Pi | CI | Independent |
| OctoPi HLS/MJPEG streaming | Stream process | `kenpi/video/` | Frame buffer only; BLOCKED on A810 Lite stream evidence | `tests/test_video_buffer.py` | Independent |
| AppCompanion registration | Companion enrolment | not implemented | NOT IMPLEMENTED. Needs design: short-lived challenge, local approval, per-device credential, revocation, TLS gateway. Static app keys rejected | none | Concept only |
