import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config


def record(url: str, title: str = "여수 이야기") -> dict:
    return config.make_record(
        title=title,
        body="여수에 전하는 이야기입니다.",
        source_url=url,
        source_name="테스트",
        license_note="테스트용",
    )


class SaveRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.output_patch = patch.object(config, "OUTPUT_DIR", self.output_dir)
        self.output_patch.start()

    def tearDown(self):
        self.output_patch.stop()
        self.temp_dir.cleanup()

    def read(self, filename: str) -> list[dict]:
        return json.loads((self.output_dir / filename).read_text(encoding="utf-8"))

    def test_merge_updates_duplicate_and_keeps_existing(self):
        config.save_records([record("https://example.test/1", "이전 제목")], "x.json")
        config.save_records(
            [
                record("https://example.test/1", "새 제목"),
                record("https://example.test/2", "두 번째"),
            ],
            "x.json",
        )

        saved = self.read("x.json")
        self.assertEqual(len(saved), 2)
        by_url = {item["출처URL"]: item for item in saved}
        self.assertEqual(by_url["https://example.test/1"]["제목"], "새 제목")

    def test_snapshot_replaces_existing(self):
        config.save_records([record("https://example.test/old")], "x.json")
        config.save_records(
            [record("https://example.test/new")],
            "x.json",
            merge_existing=False,
        )

        self.assertEqual(
            [item["출처URL"] for item in self.read("x.json")],
            ["https://example.test/new"],
        )

    def test_empty_snapshot_preserves_existing_by_default(self):
        config.save_records([record("https://example.test/good")], "x.json")
        config.save_records([], "x.json", merge_existing=False)

        self.assertEqual(
            [item["출처URL"] for item in self.read("x.json")],
            ["https://example.test/good"],
        )

    def test_empty_url_is_not_saved(self):
        config.save_records([record("")], "x.json")
        self.assertEqual(self.read("x.json"), [])


class RegionFilterTests(unittest.TestCase):
    def test_filters_records_without_target_region(self):
        kept, dropped = config.filter_by_region(
            [
                record("https://example.test/1", "순천의 설화"),
                config.make_record(
                    "완도의 설화",
                    "다른 지역 이야기",
                    "https://example.test/2",
                    "테스트",
                    "테스트용",
                ),
            ]
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 1)


if __name__ == "__main__":
    unittest.main()
