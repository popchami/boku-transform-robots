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
    (base_dir / "transform.mp4").touch()
    (base_dir / "after.mp4").touch()
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
            {"id": "transform", "duration_sec": 4, "video": "transform.mp4", "sfx": ["sfx.wav"]},
            {"id": "after", "duration_sec": 6, "video": "after.mp4", "sfx": ["sfx.wav"]},
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

    def test_non_string_title_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["title"] = 12345
            with self.assertRaises(ManifestError):
                load_manifest(_write_manifest(base, data))

    def test_captions_as_string_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["captions"] = "1文字ずつ分割されると困る文字列"
            with self.assertRaises(ManifestError):
                load_manifest(_write_manifest(base, data))

    def test_sfx_as_string_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][2]["sfx"] = "sfx.wav"
            with self.assertRaises(ManifestError):
                load_manifest(_write_manifest(base, data))

    def test_non_object_scene_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0] = "intro"
            with self.assertRaises(ManifestError):
                load_manifest(_write_manifest(base, data))

    def test_bool_duration_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["duration_sec"] = True
            with self.assertRaises(ManifestError):
                load_manifest(_write_manifest(base, data))

    def test_nan_duration_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            raw = json.dumps(data, ensure_ascii=False)
            path = base / "episode.json"
            # json標準ライブラリはNaN/Infinityを許容してdumpするため、直接埋め込む
            raw = raw.replace('"duration_sec": 3', '"duration_sec": NaN', 1)
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_infinity_duration_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            raw = json.dumps(data, ensure_ascii=False)
            raw = raw.replace('"duration_sec": 3', '"duration_sec": Infinity', 1)
            path = base / "episode.json"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(path)


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

    def test_missing_transform_video_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][2]["video"] = "missing.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "video が見つかりません" in i.message for i in issues))

    def test_video_path_traversal_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][2]["video"] = "../outside.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "パストラバーサル" in i.message for i in issues))

    def test_video_on_non_video_scene_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["video"] = "transform.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "シーンでのみ指定できます" in i.message for i in issues))

    def test_video_on_non_video_scene_is_error_even_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["video"] = "does_not_exist.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "シーンでのみ指定できます" in i.message for i in issues))
            self.assertFalse(any("見つかりません" in i.message for i in issues))

    def test_missing_after_video_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][3]["video"] = "missing.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "video が見つかりません" in i.message for i in issues))

    def test_after_without_video_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            del data["scenes"][3]["video"]
            data["scenes"][3]["image"] = "image.png"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(
                any(i.level == "error" and "video が指定されていません" in i.message for i in issues)
            )

    def test_after_image_and_video_together_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][3]["image"] = "image.png"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(
                any(i.level == "error" and "同時に指定できません" in i.message for i in issues)
            )

    def test_after_video_invalid_extension_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            (base / "after.mov").touch()
            data["scenes"][3]["video"] = "after.mov"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "拡張子" in i.message for i in issues))

    def test_transform_without_video_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            del data["scenes"][2]["video"]
            data["scenes"][2]["image"] = "image.png"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(
                any(i.level == "error" and "video が指定されていません" in i.message for i in issues)
            )

    def test_transform_image_and_video_together_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][2]["image"] = "image.png"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(
                any(i.level == "error" and "同時に指定できません" in i.message for i in issues)
            )

    def test_transform_video_invalid_extension_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            (base / "transform.mov").touch()
            data["scenes"][2]["video"] = "transform.mov"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "拡張子" in i.message for i in issues))

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

    def test_caption_with_special_char_is_not_a_warning(self) -> None:
        # render側がcaption本文をtextfile=経由で渡す（フィルタパーサを経由しない）ため、
        # ffmpegエスケープ文字を含んでいてもvalidate時点でのwarningは不要（docs/RENDER_DESIGN.md 3.5参照）。
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["captions"] = ["時刻: 12:34にへんしん！"]
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertFalse(any("エスケープ" in i.message for i in issues))

    def test_output_path_traversal_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["output"] = "output/../../outside.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "パストラバーサル" in i.message for i in issues))

    def test_output_absolute_path_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["output"] = "/etc/passwd.mp4"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "絶対パス" in i.message for i in issues))

    def test_output_without_mp4_extension_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["output"] = "output/ep001.mov"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "拡張子" in i.message for i in issues))

    def test_asset_path_traversal_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][0]["image"] = "../../etc/passwd"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "パストラバーサル" in i.message for i in issues))

    def test_asset_absolute_path_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["font"] = "/etc/passwd"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "絶対パス" in i.message for i in issues))

    def test_unknown_transition_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = _minimal_manifest_data(base)
            data["scenes"][1]["transition"] = "wipe"
            episode = load_manifest(_write_manifest(base, data))
            issues = validate_episode(episode, base)
            self.assertTrue(any(i.level == "error" and "transition" in i.message for i in issues))


if __name__ == "__main__":
    unittest.main()
