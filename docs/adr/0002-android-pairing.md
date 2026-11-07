# ADR 0002 — Android pairing

Status: Accepted (design). Implementation: Pi side implemented and tested (`kenpi/security/devices.py`, `kenpi-devices` CLI). The Android client (QR scan, Keystore key, refresh) is NOT implemented.

## Decision
One-time QR enrolment; every installation has its own identity and revocable
credentials. No shared password or static API key (the AppCompanion approach
is rejected).

## Sequence
1. Local administrator starts pairing.
2. Kenpi creates a random single-use challenge, valid 5 minutes.
3. QR carries pairing address, TLS certificate fingerprint and challenge. No permanent credential.
4. Android creates a key pair in Android Keystore, submits public key and enrolment proof.
5. Administrator explicitly approves on the Pi/head unit.
6. Kenpi issues a device credential; the challenge is invalidated.

## Credentials
| Item | Design |
| --- | --- |
| Identity | Unique per installation |
| Access token | Opaque, ~5 min, server-validated |
| Refresh credential | Random, per device, rotated on use |
| Storage on Pi | Hashed or keyed-hash records only |
| Scopes | Capability-based |
| Transport | HTTPS/WSS |
| Replacement | New enrolment, never credential copying |

No custom auth protocol, no long-lived self-contained JWTs.

## Device management
List (name, id, enrolled, last seen), revoke (immediate), re-enrol, rotate.
Reject expired, revoked and replayed challenges; rate-limit attempts.

## Acceptance
Replay, expiry, wrong-fingerprint, unapproved, revoked-token, refresh-rotation
reuse-detection and rate-limit tests.
