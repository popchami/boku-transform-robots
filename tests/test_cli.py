"""bokurobo.cli の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bokurobo.cli import main  # noqa: E402


def _write_valid_manifest(base_dir: Path) -> Path:
    (base_dir / "image.png").touch()
    data = {
        "episode_id": "ep001",
        "title": "そうじきロボ",
        "category": "daily_goods",
        "output": "output/ep001.mp4",
        "scenes": [
            {"id": "intro", "duration_sec": 3, "image": "image.png"},
            {"id": "before", "duration_sec": 3, "image": "image.png"},
            {"id": "transform", "duration_sec": 4, "image": "image.png"},
            {"id": "after", "duration_sec": 6, "image": "image.png"},
            {"id": "punchline", "duration_sec": 4, "image": "image.png"},
        ],
    }
    path = base_dir / "episode.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class CliValidateTests(unittest.TestCase):
    def test_valid_manifest_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            exit_code = main(["validate", str(manifest_path), "--base-dir", str(base)])
            self.assertEqual(exit_code, 0)

    def test_broken_manifest_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = base / "episode.json"
            manifest_path.write_text("{not json", encoding="utf-8")
            exit_code = main(["validate", str(manifest_path)])
            self.assertEqual(exit_code, 1)

    def test_manifest_with_error_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["category"] = "vehicle"
            manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            exit_code = main(["validate", str(manifest_path), "--base-dir", str(base)])
            self.assertEqual(exit_code, 1)

    def test_malformed_types_do_not_raise_unhandled_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["title"] = 12345
            data["scenes"][0]["captions"] = "分割されると困る"
            manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            # ManifestErrorはcli内で捕捉されexit 1になるべきで、例外が外に漏れてはならない
            exit_code = main(["validate", str(manifest_path), "--base-dir", str(base)])
            self.assertEqual(exit_code, 1)

    def test_output_path_traversal_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["output"] = "output/../../outside.mp4"
            manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            exit_code = main(["validate", str(manifest_path), "--base-dir", str(base)])
            self.assertEqual(exit_code, 1)


class CliRenderTests(unittest.TestCase):
    def test_render_is_not_implemented(self) -> None:
        exit_code = main(["render"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
