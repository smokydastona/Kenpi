# ADR 0004 — Backup and restore

Status: Accepted (design). Implementation: not started.

## Decision
Backups are versioned, integrity-checked archives. Restore is a transaction.
This is a separate recovery subsystem, not part of the camera or API services.

## Contents
Include: configuration, head-unit profiles, protocol evidence and fixtures,
application database, media index/metadata, user configuration, deployment
configuration, firmware source/config. Optional (separately selected):
original camera media. Exclude by default: device credentials, private keys,
secrets. A restored Pi requires fresh enrolment and credential rotation.

## Manifest
```json
{"format": "kenpi-backup", "format_version": 1, "kenpi_version": "x.y.z",
 "created_at": "ISO-8601", "components": [], "checksums": {},
 "includes_media": false}
```

## Requirements
SHA-256 verification, manifest and version validation, atomic creation,
interrupted-backup detection, capacity checks, list/inspect, never overwrite
an existing backup, destination adapter interface (first targets: local USB, SFTP).

## Restore sequence
verify integrity → validate manifest/versions → compatibility check →
pre-restore recovery snapshot → stage → validate staged tree → atomic
activation → restart services → health checks → report or roll back.
Dry-run and explicit confirmation are required. A failure never leaves Kenpi
partially overwritten; revoked credentials are never restored.

## Acceptance
Corrupt archive, truncated archive, version mismatch, disk-full, interrupted
restore and rollback tests; post-restore health check.
