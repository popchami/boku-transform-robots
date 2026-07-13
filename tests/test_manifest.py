"""bokurobo.manifest の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bokurobo.manifest import ManifestError, load_manifest, validate_episode  # noqa: E402


def _write_manifest(dir_path: Path, data: dict) -> Path:
    path = dir_path / "episode.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _minimal_manifest_data(base_dir: Path) -> dict:
    (base_dir / "image.png").touch()
    (base_dir / "audio.wav").touch()
    (base_dir / "sfx.wav").touch()
    (base_dir / "font.ttf").touch()
    return {
        "episode_id": "ep001",
        "title": "そうじきロボ",
        "category": "daily_goods",
        "font": "font.ttf",
        "output": "output/ep001.mp4",
        "scenes": [
            {
                "id": "intro",
                "duration_sec": 3,
                "image": "image.png",
                "narration_audio": "audio.wav",
                "captions": ["きょうのお題はそうじき"],
            },
            {"id": "before", "duration_sec": 3, "image": "image.png"},
            {"id": "transform", "duration_sec": 4, "image": "image.png", "sfx": ["sfx.wav"]},
            {"id": "after", "duration_sec": 6, "image": "image.png"},
            {"id": "punchline", "duration_sec": 4, "image": "image.png", "captions": ["すいこみりょく満点！"]},
        ],
    }


class LoadManifestTests(unittest.TestCase):
    def test_load_valid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            episode = load_manifest(_write_manifest(base, data))
            self.assertEqual(episode.episode_id, "ep001")
            self.assertEqual(len(episode.scenes), 5)

    def test_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_missing_field_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_manifest(Path(tmp), {"episode_id": "ep001"})
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(ManifestError):
            load_manifest(Path("/nonexistent/episode.json"))


class ValidateEpisodeTests(unittest.TestCase):
    def test_valid_manifest_has_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            errors = [i for i in issues if i.level == "error"]
            self.assertEqual(errors, [])

    def test_wrong_total_duration_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["duration_sec"] = 100
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "総尺" in i.message for i in issues))

    def test_missing_scene_id_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            del data["scenes"][0]
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "scenes の id" in i.message for i in issues))

    def test_missing_referenced_image_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["image"] = "missing.png"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "image が見つかりません" in i.message for i in issues))

    def test_output_outside_output_dir_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["output"] = "somewhere/ep001.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "output は" in i.message for i in issues))

    def test_banned_word_in_title_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["title"] = "トランスフォーマー掃除機"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "warning" and "禁止語" in i.message for i in issues))

    def test_invalid_category_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["category"] = "vehicle"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "category" in i.message for i in issues))

    def test_missing_font_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["font"] = "missing_font.ttf"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "font が見つかりません" in i.message for i in issues))

    def test_caption_with_special_char_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["captions"] = ["時刻: 12:34にへんしん！"]
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "warning" and "エスケープ" in i.message for i in issues))


if __name__ == "__main__":
    unittest.main()
