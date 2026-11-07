"""Conservative redaction of text captures before they can be shared.

Covers HTTP-style headers, key/value secrets, URL query secrets, MAC addresses,
device identifiers and GPS coordinates. Redaction is best effort: reviewed
output is still required, and binary captures (pcap) are refused rather than
guessed at. Original captures must stay private.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

REDACTED = "[REDACTED]"

_SECRET_KEYS = (
    r"password|passwd|pwd|passphrase|psk|wpa_?key|ssid|token|access_?token|"
    r"refresh_?token|session(?:_?id)?|sid|secret|api_?key|apikey|auth|"
    r"signature|sign|nonce|imei|serial(?:_?no|_?number)?|sn|device_?id|"
    r"uuid|udid|mac|bssid|lat|latitude|lon|lng|longitude|gps|location"
)

_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "header",
        re.compile(
            r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|set-cookie|"
            r"x-[\w-]*(?:token|key|secret|auth|session|device)[\w-]*)\s*:)[^\r\n]*"
        ),
        r"\1 " + REDACTED,
    ),
    (
        "bearer",
        re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 " + REDACTED,
    ),
    (
        "key_value",
        re.compile(
            rf"(?i)(?P<k>[\"']?\b(?:{_SECRET_KEYS})[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s&,;\"'<>]+)"
        ),
        r"\g<k>" + REDACTED,
    ),
    (
        "nmea",
        re.compile(r"\$G[NPLAB][A-Z]{3},[^\r\n]*"),
        REDACTED,
    ),
    (
        "mac",
        re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b"),
        REDACTED,
    ),
    (
        "coordinates",
        re.compile(r"(?<![\d.])-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}(?![\d.])"),
        REDACTED,
    ),
)


def redact_text(text: str) -> tuple[str, Counter[str]]:
    """Return redacted text and per-rule replacement counts."""
    counts: Counter[str] = Counter()
    for name, pattern, replacement in _RULES:
        text, number = pattern.subn(replacement, text)
        if number:
            counts[name] += number
    return text, counts


def redact_file(source: Path, destination: Path) -> Counter[str]:
    if source.resolve() == destination.resolve():
        raise ValueError("refusing to overwrite the original capture")
    raw = source.read_bytes()
    if b"\x00" in raw:
        raise ValueError("binary captures are not supported; export as text first")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("capture must be UTF-8 text") from exc
    redacted, counts = redact_text(text)
    destination.write_text(redacted, encoding="utf-8")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Redact a text capture for sharing")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args(argv)
    try:
        counts = redact_file(arguments.source, arguments.destination)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    total = sum(counts.values())
    print(f"{total} redactions: {dict(counts)}. Review the output before sharing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
