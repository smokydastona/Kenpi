# Head unit support

## Status: NOT VERIFIED (no head unit has been tested)

Kenpi is head-unit agnostic. Select a profile with `head_unit` in `[kenpi]`
(default `kenwood-dmx5710s`); the registry is in `kenpi/headunit/profiles.py`
and is listed read-only at `GET /api/v1/head-units`. `DisplayAdapter` and
`TouchAdapter` (`kenpi/kenwood/adapter.py`) isolate the UI from the transport.
Registry profiles are capped below E4: promoting one requires a physical
end-to-end record (see `docs/evidence-and-validation.md`).

## Design principle: the head unit owns safety cameras

- Backup camera: head unit's dedicated rear-camera input, driven by the reverse
  trigger wire, so it works with the Pi off or booting.
- Front camera: head unit's second camera / AV input (model dependent).
- The Pi only renders the Kenpi UI and talks to the 70mai A810 Lite. Kenpi is
  never on the reverse-camera path.

```
[A810 Lite] --(WiFi/USB)--> [Raspberry Pi 5] --(HDMI)--> [Head unit]
[Backup cam] --(RCA, reverse trigger)--> [Head unit] <--(RCA)-- [Front cam]
```

## Profiles

| Key | Claimed video | 2nd camera | Evidence |
|---|---|---|---|
| `kenwood-dmx5710s` | none established | unknown | E0 |
| `pioneer-dmh-w4660nex` | HDMI, AV, WebLink | AV as "2nd Camera" | E1 |
| `pioneer-dmh-wt7600nex` | HDMI | unknown | E1 |
| `sony-xav-ax8500` | HDMI | reported | E1 |
| `sony-xav-ax6000` | HDMI | reported none | E1 |
| `kenwood-dmx709s` | HDMI | unknown | E1 |
| `alpine-ilx-f511` | HDMI | unknown | E1 |
| `generic-hdmi-display` | HDMI | n/a | E0 |

## Verification limits (honest summary)

Manufacturer sites (Pioneer, Sony, Kenwood, Crutchfield) were unreachable from
the build environment, so no primary source was read. Only AI-assisted web
searches were available. They are secondary and **contradictory**:

- They broadly agree that these units have an HDMI input and a rear-camera RCA
  input, and that the DMH-W4660NEX AV input can be set to a second camera.
- They disagree on whether a generic HDMI source such as a Raspberry Pi is
  accepted, or whether the port expects a phone adapter handshake
  (HDCP/EDID/MHL-style). Treat this as the key risk for every profile.
- They agree there is **no touch return** from a plain HDMI source. Every
  profile therefore uses `TouchPath.NONE`; the UI must work display-only, or
  with steering-wheel/physical controls, unless a vendor path (e.g. WebLink)
  is separately proven.
- Some model details (Alpine iLX-F511, Sony camera counts, DMH-WT7600NEX) were
  inconsistent between results and are left as unknown.

## Verifying a head unit

1. Read the exact model's manual: HDMI input type, supported formats, camera
   inputs, reverse override, input switching.
2. Bench-test Pi HDMI output (resolution, hotplug/EDID, audio) with the real
   unit; record results.
3. Test touch return only if a documented transport exists.
4. Do not test camera controls or playback while driving.

## Adding a head unit

1. Add a `HeadUnitProfile` with conservative claims and E0/E1 evidence.
2. Add `DisplayAdapter`/`TouchAdapter` implementations only after the transport
   is evidenced.
3. Add a per-model note here or in a dedicated doc.
