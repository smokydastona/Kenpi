"""Parser for 70mai dashcam `$V02` GPS log text exports.

This module parses GPS log files already exported to an SD card by 70mai
dashcams (the `GPSData*.txt` files some 70mai apps/cameras produce
alongside recordings). It does **not** talk to a camera, a network, or any
live protocol: the input is a plain text file the user already has.

The format implemented here is ported from the community reverse-engineering
in https://github.com/sparkcyf/70mai-gps-log-viasualizer (see
`docs/camera-firmware.md` for the full, attributed research-lead entry).
That project's own analysis explicitly flags several details as *inferred
from observed data*, not vendor-confirmed:

- the speed scale factor (raw * 0.036 -> km/h),
- the accelerometer scale factor (raw * 0.01 -> g), and
- the exact absolute-time epoch of the raw timestamp column (it is not
  standard UTC; the upstream project estimates a per-recording offset from
  the video filename's embedded local time, which varies by camera timezone
  setting and is not safe to hardcode here as a universal constant).

This parser preserves those caveats: it exposes the scale factors as named
constants (so callers can see/override them) and never guesses an absolute
timestamp correction on the caller's behalf. Evidence level: E1 (community
research lead, not vendor-documented, not independently verified against
A810 Lite hardware in this repository) — see `docs/camera-firmware.md`.

GPS coordinates are personal location data. To avoid accidentally leaking
them, the CLI in this module never prints latitude/longitude unless the
caller explicitly passes ``--include-coordinates``.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

BLOCK_MARKER = "$V02"
_FIELD_COUNT = 13

# Scale factors inferred from observed sample data by the upstream research
# project, not confirmed by vendor documentation. See module docstring.
HEADING_DEGREES_PER_RAW_UNIT = 0.01
SPEED_KMH_PER_RAW_UNIT = 0.036
ACCEL_G_PER_RAW_UNIT = 0.01


class GpsLogFormatError(ValueError):
    """Raised when a GPS log line does not match the expected `$V02` format."""


@dataclass(frozen=True, slots=True)
class GpsLogRecord:
    """A single parsed GPS log data row.

    `raw_timestamp` is preserved unmodified: its epoch/offset is not
    established (see module docstring), so no absolute UTC time is derived
    here. `reserved` holds the three trailing fields whose meaning the
    upstream research project could not determine; they are kept verbatim
    rather than dropped or guessed at.
    """

    trip_index: int
    raw_timestamp: int
    fix_valid: bool
    latitude: float
    longitude: float
    heading_degrees: float
    speed_kmh: float
    accel_x_g: float
    accel_y_g: float
    accel_z_g: float
    video_filename: str
    reserved: tuple[str, str, str]

    @property
    def accel_magnitude_g(self) -> float:
        return math.sqrt(self.accel_x_g**2 + self.accel_y_g**2 + self.accel_z_g**2)


def _parse_record(line: str, *, trip_index: int, line_number: int) -> GpsLogRecord:
    fields = line.split(",")
    if len(fields) != _FIELD_COUNT:
        raise GpsLogFormatError(
            f"line {line_number}: expected {_FIELD_COUNT} comma-separated fields, "
            f"found {len(fields)}"
        )
    (
        raw_timestamp,
        fix_status,
        latitude,
        longitude,
        heading_raw,
        speed_raw,
        gx_raw,
        gy_raw,
        gz_raw,
        video_filename,
        reserved_a,
        reserved_b,
        reserved_c,
    ) = fields

    if fix_status not in ("A", "V"):
        raise GpsLogFormatError(
            f"line {line_number}: fix status must be 'A' or 'V', found {fix_status!r}"
        )

    try:
        return GpsLogRecord(
            trip_index=trip_index,
            raw_timestamp=int(raw_timestamp),
            fix_valid=fix_status == "A",
            latitude=float(latitude),
            longitude=float(longitude),
            heading_degrees=int(heading_raw) * HEADING_DEGREES_PER_RAW_UNIT,
            speed_kmh=int(speed_raw) * SPEED_KMH_PER_RAW_UNIT,
            accel_x_g=int(gx_raw) * ACCEL_G_PER_RAW_UNIT,
            accel_y_g=int(gy_raw) * ACCEL_G_PER_RAW_UNIT,
            accel_z_g=int(gz_raw) * ACCEL_G_PER_RAW_UNIT,
            video_filename=video_filename,
            reserved=(reserved_a, reserved_b, reserved_c),
        )
    except ValueError as exc:
        raise GpsLogFormatError(
            f"line {line_number}: non-numeric field ({exc})"
        ) from exc


def iter_gps_log_records(lines: Iterable[str]) -> Iterator[GpsLogRecord]:
    """Yields `GpsLogRecord`s from an iterable of raw log lines.

    Lines are grouped into trips by `$V02` markers; `trip_index` counts
    markers seen so far (starting at 0 for the first block). Raises
    `GpsLogFormatError` on the first malformed data line rather than
    silently skipping it, so corrupt input is never misreported as a clean
    parse.
    """
    trip_index = -1
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line == BLOCK_MARKER:
            trip_index += 1
            continue
        if trip_index < 0:
            raise GpsLogFormatError(
                f"line {line_number}: data row found before any {BLOCK_MARKER} marker"
            )
        yield _parse_record(line, trip_index=trip_index, line_number=line_number)


def parse_gps_log_text(text: str) -> list[GpsLogRecord]:
    return list(iter_gps_log_records(text.splitlines()))


def parse_gps_log_file(path: Path) -> list[GpsLogRecord]:
    return parse_gps_log_text(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class GpsLogSummary:
    """Aggregate, location-free statistics over a set of records."""

    trip_count: int
    record_count: int
    valid_fix_count: int
    invalid_fix_count: int
    median_speed_kmh: float | None
    max_speed_kmh: float | None
    median_accel_magnitude_g: float | None
    max_accel_magnitude_g: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "trip_count": self.trip_count,
            "record_count": self.record_count,
            "valid_fix_count": self.valid_fix_count,
            "invalid_fix_count": self.invalid_fix_count,
            "median_speed_kmh": self.median_speed_kmh,
            "max_speed_kmh": self.max_speed_kmh,
            "median_accel_magnitude_g": self.median_accel_magnitude_g,
            "max_accel_magnitude_g": self.max_accel_magnitude_g,
        }


def summarize(records: Iterable[GpsLogRecord]) -> GpsLogSummary:
    """Builds location-free aggregate statistics.

    Deliberately excludes latitude/longitude/heading/filenames: this
    summary is safe to print or share without redaction. Speed/accel
    figures are included in degrees/km-h/g units that were themselves
    derived from an inferred scale factor (see module docstring); treat
    them as approximate.
    """
    records = list(records)
    trips = {record.trip_index for record in records}
    valid_speeds = [record.speed_kmh for record in records if record.fix_valid]
    accel_magnitudes = [record.accel_magnitude_g for record in records]
    valid_fix_count = sum(1 for record in records if record.fix_valid)
    return GpsLogSummary(
        trip_count=len(trips),
        record_count=len(records),
        valid_fix_count=valid_fix_count,
        invalid_fix_count=len(records) - valid_fix_count,
        median_speed_kmh=statistics.median(valid_speeds) if valid_speeds else None,
        max_speed_kmh=max(valid_speeds) if valid_speeds else None,
        median_accel_magnitude_g=(
            statistics.median(accel_magnitudes) if accel_magnitudes else None
        ),
        max_accel_magnitude_g=max(accel_magnitudes) if accel_magnitudes else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a 70mai $V02 GPS log export and print location-free "
            "summary statistics (E1 community format, scale factors inferred; "
            "see docs/camera-firmware.md)."
        )
    )
    parser.add_argument("source", type=Path, help="Path to a GPSData*.txt export")
    parser.add_argument(
        "--include-coordinates",
        action="store_true",
        help=(
            "Also print each record's raw latitude/longitude. Off by default "
            "because GPS coordinates are personal location data."
        ),
    )
    arguments = parser.parse_args(argv)

    try:
        records = parse_gps_log_file(arguments.source)
    except (OSError, GpsLogFormatError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output: dict[str, object] = {"summary": summarize(records).as_dict()}
    if arguments.include_coordinates:
        output["records"] = [
            {
                "trip_index": record.trip_index,
                "fix_valid": record.fix_valid,
                "latitude": record.latitude,
                "longitude": record.longitude,
            }
            for record in records
        ]
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
