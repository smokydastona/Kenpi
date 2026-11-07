# A810 Lite camera firmware policy

## Policy

- **Do not flash custom or modified firmware** to the project camera. Stock
  firmware is the only behavior this project profiles; modified firmware would
  invalidate captured evidence and risks bricking the unit.
- Update only through the official 70mai app, and only deliberately and
  between recorded captures (see the checklist below).
- Do not interrupt power during an update. Never update from a weak USB port.

## Unverified leads (E0, do not act on)

These came from unattributed secondhand summaries. None is verified, none has a
confirmed source, and none is A810 Lite evidence (other-model findings are at
most E1 even when sourced).

- No official GitHub firmware repository or stable custom-firmware project is
  known for the A810 Lite.
- Official updates are said to go through the app (Device Management, Firmware
  Update) via the camera's Wi-Fi hotspot.
- Community A810/A800-family modifications are said to exist (a "kitchen"
  unpack/patch/repack toolset and SD-card `.bin` sideloading). Version
  strings (`1.3.46ww`, `1.5.40ww`) and filenames (`FW98529A.bin`,
  `FORCEUPD.txt`) are unconfirmed. The A810 is said to use a different
  processor architecture than the A800S, so any tooling may not apply.

Promote a lead only after recording its source and testing it on stock
hardware per `docs/protocol-discovery.md`.

## Attributed community research leads (E1, other models, not actioned)

Unlike the unattributed leads above, these have a named source and public
repository, so they are E1 (sourced research lead) rather than E0. They still
do not establish anything about the **A810 Lite** specifically: each targets a
different model and/or firmware revision, and none has been reproduced on
project hardware. Per project policy, none of the firmware-patching or
factory-mode procedures below are implemented in this repository.

- **[sparkcyf/70mai-A810S-telnet-toolbox](https://github.com/sparkcyf/70mai-A810S-telnet-toolbox)**
  — targets the **70mai A810Pro, model DR2830, China firmware 1.2.42** (not
  A810 Lite/DR2860). Documents a factory-mode entry gesture (five short
  presses of the second button on the first welcome screen) that reaches a
  USB-serial (`/dev/ttyGS0`) factory command interface gated by a
  `boot_sysmode` parameter and USB power-type detection, and a command table
  entry (`AT^cmd^`) that pipes shell commands through `popen`. The toolbox
  uses this to enable BusyBox `telnetd` (root, no password) on the camera's
  own Wi-Fi. The authors independently reverse-engineered this from a
  captured `main_app` ELF binary and explicitly scope every tool to that
  exact model/firmware, refusing to run on anything else. **Not reproduced
  here**: this entry sequence, if present at all on the A810 Lite, would need
  independent rediscovery against A810 Lite firmware; its own binary and
  addresses are specific to the captured A810Pro firmware and are not
  distributed by the authors.
- **[vlw/70mai-a810-cn-mod](https://github.com/vlw/70mai-a810-cn-mod)** —
  a firmware **patcher** (not a firmware distributor; it does not and will
  not ship 70mai firmware images) for **70mai A810 PRO CN, firmware 1.6.49**,
  identified by exact `main_app` MD5. It binary-patches fixed, non-PIE
  virtual addresses to unlock a language menu, timezone menu, GPS-always-on
  icon, HDR, and telnet, verifying every patched word against the stock image
  first and refusing to build on any other version. This is firmware
  reverse-engineering and binary patching of a specific proprietary image;
  Kenpi's policy (above) is to never flash modified firmware to the project
  camera, so this approach is not applicable regardless of model match.
- **[350d/70Mai_A810S_Firmware](https://github.com/350d/70Mai_A810S_Firmware)**
  — a custom firmware project for **70mai A810S, based on 1.5.40ww**.
  Notable as evidence (E1, different model) that some 70mai dashcams in this
  family ship stock firmware containing an HTTP **"Web Admin"** control
  surface, FTP, and Telnet, and that **HTTP control requests to the stock
  firmware are signature-validated** (the mod's notable change is *disabling*
  that signature check) — i.e. the stock firmware already has a request
  signing scheme. This is a concrete hint that an equivalent signed HTTP
  control API may exist on the A810 Lite, but the signing scheme, endpoints,
  and payload format are not documented in this source and are not
  established for the A810 Lite. No telnet/FTP-enabling or firmware
  rebuilding is implemented here.
- **[ClanSlum/Kino](https://github.com/ClanSlum/Kino)** — a dashcam viewer
  client for the **70mai A810** (not explicitly A810 Lite) with a BLE
  provisioning test CLI (`ble-cli/index.ts`). It documents a BLE GATT layout
  (service `fd4e`, a CRC32 data channel on characteristics `0001`/`0002`, and
  a non-CRC auth channel on `0010`/`0011`) and a reverse-engineered 70mai
  **cloud account login API** (`https://us-api.70mai.com/accountApi/V3/...`)
  using an MD5-based request-signing scheme tied to a hardcoded "channel
  magic" value. **Not reproduced here, deliberately**: Kenpi is local-only by
  design (see `docs/android-companion.md`) and does not implement a client
  for 70mai's cloud account/login API. Reproducing vendor cloud
  authentication logic is out of scope for a local gateway, is not needed for
  any Kenpi capability gate, and risks violating 70mai's terms of service;
  this repository will not add that client. The BLE GATT UUIDs are recorded
  here purely as a research lead in case BLE provisioning is revisited later
  with the physical A810 Lite and the device's own terms.
- **[sparkcyf/70mai-gps-log-viasualizer](https://github.com/sparkcyf/70mai-gps-log-viasualizer)**
  — documents the `$V02` GPS log text format exported to 70mai dashcam SD
  cards (model-agnostic log format, not tied to one hardware revision):
  comma-separated records of timestamp, fix status (`A`/`V`), latitude,
  longitude, heading (raw ÷ 100 degrees), speed (raw × 0.036 km/h, scale
  inferred not vendor-confirmed), three raw accelerometer axes (scale
  inferred as ×0.01 g), an associated video filename, and three reserved
  fields of unknown meaning. This is parsing of an **already-exported SD
  card log file**, not a live camera/network protocol, so it carries no
  camera-control risk. See `kenpi/tools/gps_log.py` for a ported, tested
  parser with the same caveats preserved.

## Why firmware patching, telnet-enabling, and cloud-auth clients are out of scope

The sources above are useful as attributed research leads, but none of them
are implemented as executable Kenpi features, because:

- Firmware patching/flashing is explicitly against this project's policy
  (see **Policy** above) regardless of which model a given tool targets.
- The factory-mode/USB-serial telnet-enabling procedure is a one-time,
  irreversible-without-a-factory-reset action on real hardware, verified by
  its own authors only against a different model/firmware; running it
  against the A810 Lite without independent rediscovery would be acting on
  E1 evidence as if it were E4, which this project's evidence rules forbid.
- 70mai's cloud login/signing API is a third-party service outside Kenpi's
  local-only architecture; reproducing its authentication scheme is not
  needed for any Kenpi capability and raises its own legal/ethical concerns
  independent of hardware risk.

## Bench checklist

1. Photograph the camera label; record model, hardware revision and serial
   privately (the serial is a device identifier: redact before sharing).
2. Record the firmware version shown in the official app before any change.
3. Capture the official app's connection sequence with
   `python -m kenpi.tools.redact` applied to any text exports before sharing.
4. If an official update is offered, first capture on the current version, then
   update, then record the new version and capture again. Never skip back or
   sideload.
5. Keep a changelog of firmware version against each capture so findings stay
   tied to a specific build.
6. Record results using the discovery template in `docs/protocol-discovery.md`
   with an evidence level for each observation.
