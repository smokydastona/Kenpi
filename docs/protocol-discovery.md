# A810 Lite protocol discovery

## Current status: BLOCKED pending physical camera evidence

The repository does not assert an A810 Lite network topology, address, service,
port, pairing procedure, authentication mechanism, session format, command,
stream codec, or media endpoint. `CameraLinkAdapter`, `CameraProtocolCodec`,
`CameraSessionProvider`, and `CommandRegistry` define the insertion points. No
production adapter is enabled.

Before implementing an operation, record camera hardware revision and firmware,
the phone-app connection sequence, Wi-Fi topology/addressing, observed services,
and sanitized request/response evidence. Assign an evidence level to every
finding. Evidence from a different 70mai model is E1 only and cannot establish
compatibility.

Unknown commands are rejected by `CameraControlService`. A successful transport
response is not treated as an acknowledgement or state verification. Never infer
authentication behavior or bypass pairing.

## Discovery record template

For each observation, record date, camera model/revision, firmware, app version,
network topology, capture method, redactions, request/response framing, observed
result, independent read-back, evidence level, and reproducibility. Do not
commit raw credentials, tokens, private video, or GPS data.

## Discovery performed and external blocker

The official [A810 Lite product page](https://www.70mai.com/global/a810lite/)
and [DR2860 A810 Lite manual](https://cdn-www.70mai.com/instructions/a810lite/DR2860-4KDashCamA810Lite-print_ML_20260130.pdf)
were located as research references. Direct retrieval failed with DNS errors.
No official independent-client command/authentication/stream specification was
established, and this repository contains no actual A810 Lite traffic captures.
Official-app Wi-Fi connectivity is not an independent-client protocol contract.

This prevents honest implementation of discovery, pairing, camera settings,
recording state/control, storage enumeration, preview reception, and camera
media retrieval. A generic network client or guessed RTSP/HTTP URL would not
resolve that blocker. No A810 protocol is substituted for A810 Lite behavior.

The command service now supplies tested policy enforcement, not a production
camera driver: E4 minimum evidence, explicit payload validation, serialization,
bounded requests, optional session validation, and conservative outcomes.
Acknowledgement and independent read-back remain separate from a response.
No runtime test mode, fake camera, or automatic write retry is connected.

To supply usable evidence, perform the official-app connection on a stationary
bench, capture only authorized device traffic, record one operation at a time,
and correlate responses with camera-side observations. Include firmware and
hardware revisions and independent restoration of each reversible setting.
Keep original captures private; publish reviewed redacted protocol findings.

## Connect-only reachability profiler

`python -m kenpi.tools.profiler --target <private-ip> [--port N ...]` completes a
TCP handshake per port and closes immediately. It sends no payload, reads no
banner, and rejects public, multicast and hostname targets. The default ports
(21, 23, 80, 554) are unverified hypotheses from other 70mai models; an open port
is E0 reachability data only and proves nothing about A810 Lite behavior. It is
not a passive sniffer: it does make connections, so run it only against your own
camera on a bench. The JSON output (`kenpi.profile/1`) can be redacted and kept
as a fixture once real results exist.
