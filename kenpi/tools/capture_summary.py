"""Summarise a redacted HAR capture into an observation inventory for human review.

This reports what was *observed*; it never defines a camera command. Observations stay
E0-E2 until the camera model and firmware are recorded and a request is replayed from
Kenpi's own client with an independently verified effect. Input that still triggers any
redaction rule is refused, so unsanitised captures cannot be summarised by accident.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from kenpi.tools.redact import redact_text

MAX_BYTES = 50 * 1024 * 1024


def load_har(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("capture too large")
    try:
        text = raw.decode("utf-8")
        document = json.loads(text)
        entries = document["log"]["entries"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("not a HAR file") from exc
    if not isinstance(entries, list):
        raise ValueError("not a HAR file")
    # Already-redacted output is a fixed point of the redactor; anything it would change is not.
    again, leftover = redact_text(text)
    if again != text:
        raise ValueError(
            f"capture still contains unredacted data ({dict(leftover)}); run kenpi.tools.redact first"
        )
    return [e for e in entries if isinstance(e, dict)]


def summarise(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, int, str], dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "statuses": set(),
            "types": set(),
            "query_keys": set(),
            "max_bytes": 0,
        }
    )
    for entry in entries:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        try:
            url = urlsplit(str(request.get("url", "")))
            port = url.port or {"http": 80, "https": 443, "rtsp": 554}.get(
                url.scheme, 0
            )
        except ValueError:
            continue
        key = (
            str(request.get("method", "?")).upper(),
            url.hostname or "?",
            port,
            url.path or "/",
        )
        group = groups[key]
        group["count"] += 1
        group["statuses"].add(response.get("status"))
        content = response.get("content") or {}
        if content.get("mimeType"):
            group["types"].add(str(content["mimeType"]).split(";")[0])
        group["query_keys"].update(k for k, _ in parse_qsl(url.query))
        size = content.get("size")
        if isinstance(size, int):
            group["max_bytes"] = max(group["max_bytes"], size)
    return [
        {
            "method": m,
            "host": h,
            "port": p,
            "path": path,
            "count": g["count"],
            "statuses": sorted(s for s in g["statuses"] if isinstance(s, int)),
            "content_types": sorted(g["types"]),
            "query_keys": sorted(g["query_keys"]),
            "max_response_bytes": g["max_bytes"],
        }
        for (m, h, p, path), g in sorted(groups.items())
    ]


def render(rows: list[dict[str, Any]], model: str, firmware: str) -> str:
    lines = [
        "# Capture observations (unverified)",
        "",
        f"Camera model: {model}  Firmware: {firmware}",
        "",
        "These rows were observed in a capture. They are not command definitions and carry no",
        "evidence above E2 until replayed from Kenpi's own client with an independently",
        "verified camera-side effect.",
        "",
        "| Method | Host:port | Path | Count | Status | Content types | Query keys |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['method']} | {r['host']}:{r['port']} | `{r['path']}` | {r['count']} | "
            f"{','.join(map(str, r['statuses'])) or '-'} | {','.join(r['content_types']) or '-'} | "
            f"{','.join(r['query_keys']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise a redacted HAR capture")
    parser.add_argument("har", type=Path)
    parser.add_argument(
        "--model", required=True, help="exact camera model, e.g. 'A810 Lite'"
    )
    parser.add_argument(
        "--firmware", required=True, help="firmware version read from the camera"
    )
    arguments = parser.parse_args(argv)
    try:
        rows = summarise(load_har(arguments.har))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render(rows, arguments.model, arguments.firmware))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
