# Kenwood DMX5710S integration

## Status: NOT VERIFIED

No claim is made that the head unit supports HDMI input, Android Auto, USB touch,
or any other particular path. `DisplayAdapter` and `TouchAdapter` isolate a
future verified integration from the UI.

Confirm the exact display input, video format/resolution, touch return path,
power behavior, and head-unit menu requirements using the physical DMX5710S
manual and hardware. Gate 3 requires the Kenpi interface on the actual unit and
physical touch interaction controlling the running application; a desktop or
browser simulation is insufficient.

## Architectural decision

The Android companion remains an optional diagnostic client. Its ordinary
launcher/Compose UI is not an implemented Android Auto application and is not
the Kenwood-facing display adapter. Phone projection support alone does not
prove a usable Raspberry Pi display output or touch return channel.

Official references identified for this exact model:

- [DMX5710S product page](https://www.kenwood.com/usa/car/caraudio/navigation_multimedia/dmx5710s/)
- [Full reference manual](https://manuals.jvckenwood.com/download/files/B5K-1159_ref_K_En_20b.pdf)
- [Instruction manual](https://manuals.jvckenwood.com/download/files/B5K-1159-20b.pdf)
- [Android for Cars supported application models](https://developer.android.com/training/cars)

Direct retrieval of the manufacturer pages/manuals failed with DNS errors in
this environment. These are research references, not a verified connector,
signal-format, mirroring, or touch specification. No concrete bidirectional
Pi-to-DMX5710S path has been established. Implementing a display driver before
that evidence exists would invent compatibility.

The required evidence is the exact head-unit revision/manual, chosen supported
video/projection path, resolution and format, touch-return transport, bench
wiring, and a physical end-to-end demonstration with the real camera. Do not
test camera controls or playback while driving.
