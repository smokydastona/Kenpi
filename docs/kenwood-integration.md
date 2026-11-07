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
- [2024 firmware update page (covers DMX5710S/DMX50S/DMX500S)](https://www.kenwood.com/cs/ce/mm/firmware/2024/2024d/eng.html)

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

## Official firmware update page (E2, directly confirms the DMX5710S)

JVCKENWOOD's own firmware update page,
`https://www.kenwood.com/cs/ce/mm/firmware/2024/2024d/eng.html`, could not be
fetched directly in this environment (same DNS block noted above), but its
content was provided by the user. It is recorded here as **E2** evidence
(official vendor source, content transcribed rather than fetched by Kenpi
tooling) because it directly names the DMX5710S, unlike every other lead in
this document:

- Confirms the **"2024MODEL"** firmware package (file `DMX5_04.zip`, listed
  update history dated 2025-05-19 through 2026-04-21, each entry reading only
  "Improved stability" or "Improved functions" with no technical detail)
  applies to exactly three units: **DMX5710S, DMX50S, DMX500S**. This is the
  first directly-sourced, official confirmation that the project's exact head
  unit is still receiving firmware updates and that it shares a firmware
  family with the DMX50S/DMX500S.
- Confirms the update is distributed and applied via a **FAT32 USB memory
  stick** inserted into the head unit, with the unit displaying for "more
  than 5 minutes" during the flash — corroborating, from an official rather
  than community source, the USB-mass-storage-based update/customization
  path already noted as E1 evidence from
  `ggerihun/Kenwood-DMX8020DABS-suzuki-boot-screen` above.
- **Reinforces the project's own policy, this time with an explicit legal
  basis**: the page's Software License Agreement states "Modification,
  alteration, translation, merging, reverse compiling, or reverse engineering
  of this software is prohibited," and that JVCKENWOOD is the sole owner of
  all copyright and intellectual property in the firmware and its
  accompanying documents. Kenpi will not reverse-engineer, extract, patch, or
  redistribute DMX5710S firmware images for this reason, independent of the
  project's own E0-E5 evidence policy. Any future Gate 3 work must rely only
  on the head unit's documented, vendor-supported external interfaces (HDMI
  input, USB media, Android Auto/CarPlay projection where applicable) rather
  than firmware-level modification.
- The changelog text itself ("Improved stability" / "Improved functions")
  contains no information about video input modes, touch architecture, or any
  other detail relevant to Kenpi's display/touch integration, so it does not
  change the NOT VERIFIED status above.

## Other smokydastona repositories reviewed (E0, not actioned)

All 19 other public repositories under the `smokydastona` GitHub account were
reviewed for code reusable toward Gate 1 (camera HTTP/CGI control), Gate 2
(video stream parsing), or Gate 3 (Kenwood touch/display integration). None
contain an applicable protocol, driver, or transport layer; every one is
either a different hardware target or a different problem domain:

- **`joycon-to-keyboard`** — an ESP32 Bluetooth-Classic-HID-to-USB-HID bridge
  (Joy-Con controller to PC keyboard/mouse/gamepad), plus a PyQt6 "Bind
  Bandit" helper app with a browser-based WebUSB/WebSerial flasher. This is
  output-side USB HID emulation for a PC, not input-side capture of a car
  head unit's touchscreen; its `hotspot_positions.json` defines clickable
  regions in the helper app's own remapping UI, not touch coordinates read
  from Kenwood hardware. **Not applicable.**
- **`Mordecai-AI-Android`** — an AI assistant project with a guarded outbound
  network layer (`net_proxy/`: domain allowlist, per-minute rate limit,
  request logging via a `SafeHttpClient`/`PolicyEngine`/`StateStore` stack).
  Conceptually similar in spirit to the directive's requested "protocol
  discovery isolation layer," but it is a multi-domain AI-API egress guard,
  not a camera/head-unit protocol client, and Kenpi's camera control already
  serializes requests to a single local device with its own evidence gates
  (`kenpi/camera/control.py`) while `kenpi/tools/redact.py` already covers
  the directive's packet-capture secrets-redaction requirement. **No code
  ported** — the problems these two projects solve only partially overlap,
  and the existing Kenpi mechanisms are already equivalent or stricter for
  Kenpi's narrower single-device use case.
- **`minecraft_telemetery`** (Bass Shaker Telemetry) — a Minecraft mod that
  turns gameplay events into tactile audio for bass shakers. Unrelated
  domain (game haptics, not vehicle hardware). **Not applicable.**
- **`dual-cm6206-driver`** / **`cm6206-dual_input_bass-shaker`** — experimental
  dual-input USB sound-card driver/router projects for bass-shaker audio
  routing. Unrelated domain (USB audio class driver internals), and Kenpi
  has no audio subsystem to apply this to. **Not applicable.**
- **`Solar-LORA-Greenhouse-Automation`** — an ESP32-S3 greenhouse controller
  (LoRa/MQTT telemetry, vent servos, Wi-Fi setup portal). Different hardware
  target and problem domain; no 70mai or Kenwood protocol content. Notably
  uses the same "reality status table" / explicit-implementation-boundary
  documentation discipline Kenpi already follows, but that is a documentation
  convention, not code, so there is nothing to port. **Not applicable.**
- **`Adaptive-Minecraft-Mob-Ai`**, **`Adaptive-Mob-Ai-Fabric_Fork`**,
  **`adaptive-ai-federation-logs`**, **`PlayerSync-Plus`**, **`KR-MBLEG-RD`**,
  **`Runa-Waystones`**, **`Runa-Waystones-ServerSide`**,
  **`Hydraulic-Phlodgate_Add-on`**, **`dead-structures`** — Minecraft
  mods/add-ons and related ML experiments. Unrelated domain. **Not
  applicable.**
- **`S-NDB-UND`**, **`S-NDB-UND-vs-code`** — a standalone sound tool and its
  VS Code extension. Kenpi has no audio subsystem for this to integrate
  with, and the tool does not touch any car-hardware protocol. **Not
  applicable.**
- **`joycon-to-keyboard`'s** sibling tools, and **`gardening`** — a personal
  gardening notes repository with no technical overlap. **Not applicable.**

Conclusion: no other `smokydastona` repository contains an HTTP/CGI client
for 70mai cameras, a video transport/decoder, or a Kenwood touch/display
protocol implementation. Gates 1–3 remain NOT VERIFIED; this review changed
no code.
