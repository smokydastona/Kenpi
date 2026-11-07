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
