import unittest
from pathlib import Path

from kenpi.tools.gps_log import (
    GpsLogFormatError,
    iter_gps_log_records,
    parse_gps_log_file,
    parse_gps_log_text,
    summarize,
)

_SAMPLE_LOG = """
$V02
1700000000,A,37.774900,-122.419400,9000,2500,10,-5,98,NO20231114-120000-000001F.MP4,0,0,0
1700000001,A,37.774950,-122.419450,9050,2600,12,-4,97,NO20231114-120000-000001F.MP4,0,0,0
1700000002,V,0.000000,0.000000,0,0,0,0,0,NO20231114-120000-000001F.MP4,0,0,0
$V02
1700001000,A,37.775900,-122.420400,18000,0,0,0,100,NO20231114-130000-000002F.MP4,0,0,0
""".strip("\n")


class GpsLogTests(unittest.TestCase):
    def test_parses_blocks_as_trips(self) -> None:
        records = parse_gps_log_text(_SAMPLE_LOG)
        self.assertEqual(len(records), 4)
        self.assertEqual([record.trip_index for record in records], [0, 0, 0, 1])

    def test_scales_heading_speed_and_accel(self) -> None:
        records = parse_gps_log_text(_SAMPLE_LOG)
        first = records[0]
        self.assertAlmostEqual(first.heading_degrees, 90.0)
        self.assertAlmostEqual(first.speed_kmh, 2500 * 0.036)
        self.assertAlmostEqual(first.accel_x_g, 0.10)
        self.assertAlmostEqual(first.accel_y_g, -0.05)
        self.assertAlmostEqual(first.accel_z_g, 0.98)
        self.assertGreater(first.accel_magnitude_g, 0.9)

    def test_invalid_fix_is_preserved_not_dropped(self) -> None:
        records = parse_gps_log_text(_SAMPLE_LOG)
        invalid = [record for record in records if not record.fix_valid]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0].trip_index, 0)

    def test_reserved_fields_preserved_verbatim(self) -> None:
        records = parse_gps_log_text(_SAMPLE_LOG)
        self.assertEqual(records[0].reserved, ("0", "0", "0"))

    def test_rejects_wrong_field_count(self) -> None:
        with self.assertRaises(GpsLogFormatError):
            list(iter_gps_log_records(["$V02", "1700000000,A,1.0,2.0"]))

    def test_rejects_bad_fix_status(self) -> None:
        bad = "$V02\n1700000000,X,1.0,2.0,0,0,0,0,0,f.MP4,0,0,0"
        with self.assertRaises(GpsLogFormatError):
            list(iter_gps_log_records(bad.splitlines()))

    def test_rejects_non_numeric_field(self) -> None:
        bad = "$V02\nNOTANUMBER,A,1.0,2.0,0,0,0,0,0,f.MP4,0,0,0"
        with self.assertRaises(GpsLogFormatError):
            list(iter_gps_log_records(bad.splitlines()))

    def test_rejects_data_before_any_marker(self) -> None:
        with self.assertRaises(GpsLogFormatError):
            list(iter_gps_log_records(["1700000000,A,1.0,2.0,0,0,0,0,0,f.MP4,0,0,0"]))

    def test_blank_lines_are_ignored(self) -> None:
        text = "\n\n$V02\n\n1700000000,A,1.0,2.0,0,0,0,0,0,f.MP4,0,0,0\n\n"
        records = parse_gps_log_text(text)
        self.assertEqual(len(records), 1)

    def test_summarize_excludes_location_fields(self) -> None:
        records = parse_gps_log_text(_SAMPLE_LOG)
        summary = summarize(records).as_dict()
        self.assertNotIn("latitude", summary)
        self.assertNotIn("longitude", summary)
        self.assertEqual(summary["trip_count"], 2)
        self.assertEqual(summary["record_count"], 4)
        self.assertEqual(summary["valid_fix_count"], 3)
        self.assertEqual(summary["invalid_fix_count"], 1)
        self.assertIsNotNone(summary["median_speed_kmh"])
        self.assertIsNotNone(summary["max_speed_kmh"])

    def test_summarize_handles_no_valid_fixes(self) -> None:
        text = "$V02\n1700000000,V,0,0,0,0,0,0,0,f.MP4,0,0,0"
        records = parse_gps_log_text(text)
        summary = summarize(records).as_dict()
        self.assertIsNone(summary["median_speed_kmh"])
        self.assertIsNone(summary["max_speed_kmh"])

    def test_parse_gps_log_file_reads_from_disk(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "GPSData000001.txt"
            path.write_text(_SAMPLE_LOG, encoding="utf-8")
            records = parse_gps_log_file(path)
            self.assertEqual(len(records), 4)


if __name__ == "__main__":
    unittest.main()
