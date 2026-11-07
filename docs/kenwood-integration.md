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

## Community research leads reviewed (not actioned)

The following public repositories were investigated as potential research
leads for the Kenwood DMX5710S integration (Gate 3). None of them establish a
Pi-to-DMX5710S video or touch transport, so none change the status above.

- **[ggerihun/Kenwood-DMX8020DABS-suzuki-boot-screen](https://github.com/ggerihun/Kenwood-DMX8020DABS-suzuki-boot-screen)**
  — the only lead here targeting the correct product family (Kenwood
  DMX-series Android-based car multimedia receivers; DMX8020DABS rather than
  DMX5710S). It documents a USB-based custom boot-splash workflow: a FAT32
  USB stick with an `image1.bmp` and a config file, a touch-corner gesture
  (hidden service-menu entry, not a documented consumer feature) to reach an
  "Opening Customize" screen, and a confirmation/rejection color code. This
  is E1 evidence (sourced, single author, different exact model, unverified
  against the DMX5710S) of two things worth recording: (1) DMX-series units
  accept USB mass-storage input and have an touch-gesture-gated hidden
  service/diagnostic menu, and (2) that menu's "Opening Customize" screen
  only replaces the static startup bitmap — it is not a live video input
  selector, touch-passthrough mode, or any other path relevant to Kenpi's
  live preview or touch-control goals. **Not actioned**: it does not move
  Gate 3 forward, and the hidden-menu gesture is not reproduced or
  documented further here since it was not observed on the actual DMX5710S
  and has no bearing on Kenpi's camera/touch integration.
- **[tonyc/open890](https://github.com/tonyc/open890)**,
  **[tonyc/kenwood_thd74_live](https://github.com/tonyc/kenwood_thd74_live)**,
  **[AG7GN/kenwood](https://github.com/AG7GN/kenwood)**,
  **[LA3QMA/TM-V71_TM-D710-Kenwood](https://github.com/LA3QMA/TM-V71_TM-D710-Kenwood)**,
  **[wf-group/wfview](https://github.com/wf-group/wfview)** — all target
  Kenwood **amateur radio transceivers** (TS-890S, TH-D74, TM-V71A/TM-D710G),
  a completely different Kenwood product line from the DMX-series car
  multimedia receivers. These use the CAT serial command protocol over an
  RS-232 "PC port" or a LAN control port designed for frequency/mode/PTT
  control of a radio — there is no car head-unit display, touch input, or
  video path involved at all. **Not applicable and not actioned**: none of
  this protocol, hardware, or control surface exists on, or is relevant to,
  the DMX5710S.
- **[erikd7/kenwood-sound-ui](https://github.com/erikd7/kenwood-sound-ui)**
  — a personal multi-room audio source-switcher project (Snapcast/Plexamp/
  Spotify) that happens to be named "Kenwood Sound" by its author; it has no
  connection to the Kenwood brand or any Kenwood hardware. **Not applicable**.
