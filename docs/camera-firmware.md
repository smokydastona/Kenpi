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
