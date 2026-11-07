# ADR 0003 — Local-first remote access with WireGuard

Status: Accepted (design). Implementation: not started.

## Boundaries
1. Local (default): head unit, local dashboard, locally paired devices, LAN API.
2. Remote (explicitly enabled): WireGuard tunnel, plus Kenpi authentication, per-device
   authorisation and encrypted WebSocket.
3. Public internet: disabled. No exposed API/WebSocket, no unauthenticated
   camera endpoints, no UPnP port forwarding.

## Rules
- Remote access is off by default and needs explicit administrator activation.
- WireGuard never replaces application auth. No unauthenticated fallback.
- Separate local and remote policies; per-device scopes; rate limiting and audit log.
- Internal service ports are never exposed. Remote access can be revoked
  without deleting local configuration.
- No third-party relay, account or subscription is a core dependency.
- The gateway is a security boundary in front of Kenpi Core, not a second Core.
- The existing loopback-only unauthenticated API is unchanged.

## Acceptance
Default-off test, policy-separation tests, revoked device refused over the
tunnel, audit entries, and a config check that nothing binds publicly.
WireGuard configuration is generated; key handling must avoid backing up private keys (ADR 0004).
