# Android control centre: requirements versus code

The phone is intended to become the primary interface. This file records what exists on
this branch, not what is planned. Status words: IMPLEMENTED (code + automated tests),
UNVERIFIED (code exists, never run on a device), NOT BUILT, BLOCKED.

## Trust model change

Before, the gateway only read state and the phone could never run anything. Now a paired
phone can run operations from a fixed allowlist, only if its device was paired with the
`ops.read` / `ops.manage` scopes. The assistant's approval rule is unchanged: the phone
still cannot approve assistant actions. There is no shell, no free-form command and no
root endpoint.

## Operation broker (`kenpi/management/broker.py`, `GET /api/v1/ops`, `POST /api/v1/ops/run`)

| Operation | Scope | Risk | Status |
| --- | --- | --- | --- |
| list_services, service_status, process_list | ops.read | read | IMPLEMENTED |
| service_restart (allowlisted units only) | ops.manage | confirm | IMPLEMENTED and tested with a fake runner; UNVERIFIED on a Pi |

Guarantees (tested): scope check, strict argument validation, unit allowlist, single-use
confirmation token bound to device + operation + arguments and expiring after 60 s,
bounded pending tokens, per-device rate limit, audit record for every attempt, failures
reported as failures.

Known limit: `kenpi-status.service` runs with `NoNewPrivileges` and no capabilities, so
`systemctl restart` will be refused until a polkit rule for the `kenpi` user is installed.
That rule is not shipped. A refusal is shown to the user, not hidden.

## Requirement map

| Area | Status |
| --- | --- |
| Pairing, Keystore key, pinned TLS, token refresh, WebSocket resume | IMPLEMENTED; `:core` interop tested against the real gateway; app UI compiled only in CI, no device run |
| Home / health / vehicle / camera status screens | IMPLEMENTED as generic key-value cards; not the designed Material 3 dashboard |
| Pi tab: service status and confirmed restart | IMPLEMENTED in code (new, UNVERIFIED on a device) |
| Assistant chat, voice input, read-aloud, log fetch/upload | IMPLEMENTED |
| Camera preview, controls, media, download | BLOCKED: A810 Lite protocol unverified |
| **Pi Desktop (screen view, pointer, keyboard, clipboard)** | NOT BUILT. Needs a capture/transport service on the Pi (WayVNC or an X11 equivalent, chosen after inspecting the installed display stack), a gateway-authenticated tunnel, a separate revocable scope, and an RFB client on Android. Nothing here may be faked with screenshots |
| Reboot, shutdown, packages, updates, filesystem, config editing, Wi-Fi/Bluetooth, GPIO | NOT BUILT. Each needs its own broker operation, argument validation and tests; power operations must also never interrupt the dashcam's independent power |
| Process management beyond listing | NOT BUILT |
| Premium theme, six-destination navigation, tablet layout, notifications, advanced mode | NOT BUILT (dynamic-colour Material 3 theme exists) |
| Settings: permissions per device, automation | NOT BUILT; device scopes are chosen at pairing on the Pi |

Nothing on this page is hardware-verified.
