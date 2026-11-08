# Raspberry Pi 5 notes

Every number below comes from third-party published benchmarks (Raspberry Pi, Tom's Hardware,
Core Electronics, Jeff Geerling) supplied by the project owner. **None were measured on Kenpi's
Pi.** Treat them as planning inputs, not results. `kenpi.tools.profiler` is the route to real
numbers once the hardware is available.

| Topic | Published figure | Consequence for Kenpi |
| --- | --- | --- |
| CPU | about 2.2-2.4x Pi 4 (Geekbench 6) | Speech models that were too slow on a Pi 4 are candidates, not guarantees; Moonshine/MeloTTS latency is still unmeasured |
| Video decode | hardware HEVC only; no hardware H.264 decoder | A810 streams that turn out to be H.264 would be decoded in software. Codec is unknown (protocol unverified), so the live-preview budget cannot be set yet |
| Video encode | no hardware H.264 encoder; software encode of 1080p30 used about 60% of one core | Any Pi-side re-encode (e.g. for a Pi Desktop stream) costs CPU shared with voice |
| Storage | microSD about 85-90 MB/s read; NVMe over PCIe about 360-400 MB/s | Media and backup retention plans should assume SD unless an NVMe HAT is fitted |
| Cooling | uncooled Pi 5 reaches 80-85 C in under 30 s of sustained load and throttles | Voice inference plus video can sustain load; an active cooler is advisable |
| Power | 5 V/5 A (27 W) supply needed for full speed with USB peripherals | The vehicle power path must supply this; see `docs/vehicle-power-management.md` |

## What Kenpi checks

`board_power` (diagnostics, host subsystem) reads the board model, the supply current the
firmware advertises, and whether a `pwm-fan` cooling device exists.

- WARN with fault `host.psu_limited` only for a Pi 5 whose supply advertises under 5000 mA.
- The mA unit of `/proc/device-tree/chosen/power/max_current` follows firmware documentation and is
  **unverified on this project's hardware**. If a real Pi reports something else, fix the check.
- Fan presence is reported as evidence only. Its absence never raises a fault, and a fan being
  present says nothing about how well the board is actually cooled; CPU temperature and the
  throttle flags (`cpu_temperature`, `throttling`) remain the authority.
- Not a measurement of what the load draws.
