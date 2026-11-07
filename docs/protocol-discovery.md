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
