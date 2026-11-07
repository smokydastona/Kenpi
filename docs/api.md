# HTTP API (v1)

Loopback-only and read-only. Every route is `GET`; anything else is rejected.
Requests are rate-limited (50 req/s sustained, burst 100; excess returns `429`)
and sockets time out after 10 s. A disabled capability returns a structured
`{"available": false, "state", "evidence", "reason"}` body, never fabricated data.

| Route | Behavior |
| --- | --- |
| `/healthz` | Host health; `503` when collection fails. |
| `/api/v1/capabilities` | Declared capabilities with state and evidence level. |
| `/api/v1/status` | Version, uptime, coordinator state. |
| `/api/v1/diagnostics` | Version, selected head unit, capability states, status. |
| `/api/v1/head-units` | Head-unit profiles (all unverified) and the selected key. |
| `/api/v1/camera` | Disabled: no verified A810 Lite protocol. |
| `/api/v1/preview` | Disabled: no verified A810 Lite stream. |
| `/api/v1/vehicle` | `enabled: false` unless `vehicle_uart_enabled`; otherwise live link snapshot (link state, power state, ignition, heartbeat age, shutdown request/ack, rejected/replayed frame counts). |
| `/api/v1/media` | Downloaded copies in `media_directory` plus storage space; camera listing reported disabled. |
| `/api/v1/media/<name>` | Streams a local copy. Names are validated; symlinks and traversal return `404`. |

Remote access remains out of scope: a separate authenticated TLS gateway is
required (see `docs/android-companion.md`).
