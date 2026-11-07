# Vehicle diagnostics research notes (K-Line / OBD-II)

**Status: unverified research.** These notes were supplied by the project owner
from a third-party summary. Kenpi's maintainers have not reviewed the linked
repositories' code or licences, have not opened the cited service manual, and
have not connected anything to a vehicle. Nothing here is implemented in the
ESP32 firmware or wired into the Pi service.

## Scope difference

Kenpi's ESP32-S3 controller currently only reports ignition/power state and
shutdown handshakes to the Pi over UART (`docs/vehicle-power-management.md`).
Reading ECU data over a diagnostic bus is a separate, optional capability.

## Claims to verify before relying on them

- The 2005 Honda Fit/Jazz service manual is reported to show a 16-pin DLC with
  pin 7 = K-Line, pin 16 = battery positive, pin 4 = chassis ground and pin 5 =
  signal ground. Confirm against the manual for the exact market/engine, then
  against the physical car with a multimeter.
- Whether the car's ECU answers generic ISO 9141-2 / ISO 14230 (OBD-II) requests
  is unknown until tested. Honda-specific protocols are a different matter.

## Candidate references (not reviewed, licences unchecked)

| Project | Reported use |
|---|---|
| muki01/OBD2_K-line_Reader and its K-Line library | ESP32 ISO 9141-2 / 14230 reader |
| OBD2 CAN reader/library, ESP32 OBD-II component | CAN (TWAI) and ELM327 variants; probably irrelevant if the car is K-Line |
| HondaECU, ArduinoHondaOBD, P30OBD, ESP32 Car Tool | Honda-specific protocol research; several target older ECUs or a different connector, and HondaECU includes ROM read/write functions Kenpi must not use |

Check each licence before copying any code. Kenpi's voice catalogue is MIT-only;
no licence rule has been decided for diagnostic code.

## Hardware cautions

- The 12 V K-Line must never reach an ESP32 GPIO. An automotive K-Line
  transceiver (the summary suggests an L9637D) and transient, reverse-polarity
  and supply protection are required; none is designed or specified here.
- Read-only only: no actuator tests, no ECU memory or ROM writes, no
  programming sessions.
- The exact UART pins for any K-Line transceiver must come from the verified
  board pinout (`docs/firmware-inventory.md`); none is assigned.

## What exists in the repository

`kenpi/vehicle/kline.py` is a transport-free frame codec for ISO 9141-2 and
ISO 14230 headers. It encodes only SAE J1979 services 01, 02, 03 and 09 and
raises for any other service, verifies checksums and lengths, reports ECU
negative responses as errors and decodes three PIDs (coolant temperature, engine
speed, vehicle speed). `tests/test_kline.py` covers it with host-only fixtures
(one known standard request frame and hand-built responses). There is no
slow-init/fast-init handling, no timing, no serial transport, no C++ port and
no capability entry: it is not validated against a real ECU and Kenpi does not
claim vehicle diagnostics.

## Hardware acceptance (not performed)

1. Confirm the DLC pinout with a multimeter on the actual car, engine off.
2. Bench the transceiver circuit against a K-Line simulator before the car.
3. Send a single mode 01 PID 00 request and record the raw bytes in a sanitized
   capture; only then add replay tests from that capture.

## Relay protocol (Pi <-> Heltec controller)

Added to the Kenpi vehicle link, mirrored in Python (`kenpi/vehicle/messages.py`)
and C++ (`firmware/include/kenpi/messages.h`) and locked by the shared fixtures
in `protocol/vehicle_frames.txt` (7 fixtures, all checked by both test suites):

- `DIAG_REQUEST` (0x05, Pi -> controller): `u16 request_id, u8 service[, u8 pid]`.
  Only services 01, 02, 03, 09 are representable; 01/02/09 need a pid, 03 takes
  none. The Python constructor and the C++ `ParseDiagnosticRequest` both reject
  everything else, so the controller is required to refuse it too.
- `DIAG_RESPONSE` (0x06, controller -> Pi): `u16 request_id, u8 status, data`
  with status OK / TIMEOUT / NEGATIVE_RESPONSE / BUS_UNAVAILABLE / REJECTED.
  OK must carry data; only OK and negative responses may.

`VehicleLinkService.diagnose(service, pid, timeout)` sends one request at a time
(only while the controller link is `connected`), matches the response by
`request_id` and returns the controller-reported status, raw hex and, for the
three known PIDs, a decoded value. A missing answer is `link_timeout`, never a
value. Tested with an in-memory stream.

Still **not** done: the firmware does not implement the K-Line side (no
transceiver driver, no 5-baud/fast init, no timing), no pins are assigned,
nothing calls `diagnose()` from the assistant, API or UI, and nothing has been
run against a Heltec board or a car. Full vehicle diagnostics therefore does not
exist yet; this is the verified Pi/controller message contract it will use.

## Repository links checked on GitHub (2026-11-07)

Existence, licence and last push were read from the GitHub API; the code was not
reviewed and nothing was tested on a vehicle. "Fit/Jazz 2005" compatibility is
unestablished for every entry.

| Repository | Licence | Notes |
|---|---|---|
| https://github.com/muki01/OBD2_K-line_Reader | GPL-3.0 | ESP32 ISO 9141-2 / 14230 reader |
| https://github.com/muki01/OBD2_KLine_Library | GPL-3.0 | K-Line library (the `OBD2_K-line_Library` name redirects here) |
| https://github.com/muki01/OBD2_CAN_Bus_Reader | GPL-3.0 | CAN variant |
| https://github.com/muki01/OBD2_CAN_Bus_Library | GPL-3.0 | CAN (TWAI) library |
| https://github.com/muki01/OBD2-Diagnostic-UI | MIT | UI for the above |
| https://github.com/Artid1994/Honda-K-Line-smart-guage-esp32-project-v1 | none stated | Honda K-Line ESP32 gauge; no licence means no reuse rights |
| https://github.com/andreibaw/Honda_K-Line_KWP2000 | GPL-3.0 | Honda KWP2000 research |
| https://github.com/aeaphichart/Honda_K-line | BSD-2-Clause | Honda K-Line |
| https://github.com/aeaphichart/HondaECU-1 | GPL-3.0 | ECU tool incl. flashing; do not use write functions |
| https://github.com/roccofz1/HondaECU | GPL-3.0 | K-Line reverse engineering (2018) |
| https://github.com/kerpz/ArduinoHondaOBD | none stated | Honda to ELM/OBD-II translation; pre-2002 ECUs |
| https://github.com/kerpz/ArduinoHondaUNI | none stated | its successor |
| https://github.com/softr8/p30obd | none stated | older P30 ECUs, different connector |
| https://github.com/VaAndCob/ESP32-Bluetooth-OBD2-Gauge | other | ELM327 gauge |
| https://github.com/terrafirma2021/ESP32-S3-BLE-ELM327 | GPL-3.0 | BLE ELM327 |
| https://github.com/Gnzlt/ESP32-S3-Touch-OBD2 | MIT | touch OBD-II gauge |
| https://github.com/erdemerciyas/ESP32-S3-OBD2 | MIT | ESP32-S3 OBD-II |
| https://github.com/MagnusThome/RejsaCAN-ESP32 | none stated | CAN board |
| https://github.com/cozadizzle/esp32-car-tool | none stated | possibly the "ESP32 Car Tool"; unconfirmed |
| https://github.com/HelTecAutomation/Heltec_ESP32 | NOASSERTION | Heltec board support |
| https://github.com/Wexter/ESP32-Motronic-4.1-K-Line-diagnostics | none stated | different ECU |

Not found: `theAgingApprentice/HondaECU-1` (404). The Heltec V3 pin numbers
quoted in the source summary (including the proposed GPIO4/GPIO5 UART) are
unverified and are not adopted; Kenpi assigns no pin until the board revision
and pinout are verified.

Licensing: GPL-3.0 code cannot be copied into Kenpi without deciding to relicense
or isolate it; "none stated" means no permission to copy. Kenpi's own K-Line
codec is written from the public SAE J1979 / ISO message formats instead.
