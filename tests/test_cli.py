"""bokurobo.cli の単体テスト（標準ライブラリ unittest のみ使用）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bokurobo.cli import main  # noqa: E402
from bokurobo.render import CommandStep, RenderError, RenderResult, WriteFileStep  # noqa: E402


def _write_valid_manifest(base_dir: Path) -> Path:
    (base_dir / "image.png").touch()
    (base_dir / "transform.mp4").touch()
    (base_dir / "after.mp4").touch()
    data = {
        "episode_id": "ep001",
        "title": "そうじきロボ",
        "category": "daily_goods",
        "output": "output/ep001.mp4",
        "scenes": [
            {"id": "intro", "duration_sec": 3, "image": "image.png"},
            {"id": "before", "duration_sec": 3, "image": "image.png"},
            {"id": "transform", "duration_sec": 4, "video": "transform.mp4"},
            {"id": "after", "duration_sec": 6, "video": "after.mp4"},
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
    """render_episode 自体の挙動は tests/test_render.py で検証する。
    ここではCLIの引数配線・戻り値/例外に応じた終了コードのみを確認する。"""

    def test_render_without_manifest_arg_exits_via_argparse_error(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            main(["render"])
        self.assertEqual(cm.exception.code, 2)

    def test_render_broken_manifest_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = base / "episode.json"
            manifest_path.write_text("{not json", encoding="utf-8")
            exit_code = main(["render", str(manifest_path)])
            self.assertEqual(exit_code, 1)

    def test_render_dry_run_reports_plan_and_exits_zero(self) -> None:
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            fake_plan = [
                WriteFileStep(path=base / "concat_list.txt", content="file 'a.mp4'\n"),
                CommandStep(description="concat", argv=["ffmpeg", "-f", "concat"], produces=base / "final.mp4"),
            ]
            fake_result = RenderResult(plan=fake_plan, output_path=base / "output" / "ep001.mp4", dry_run=True)
            stdout = io.StringIO()
            with mock.patch("bokurobo.cli.render_episode", return_value=fake_result) as mock_render, contextlib.redirect_stdout(
                stdout
            ):
                exit_code = main(["render", str(manifest_path), "--base-dir", str(base), "--dry-run"])
            self.assertEqual(exit_code, 0)
            mock_render.assert_called_once()
            self.assertTrue(mock_render.call_args.kwargs["dry_run"])
            output = stdout.getvalue()
            self.assertIn("[write]", output)
            self.assertIn("[command]", output)
            self.assertIn("concat", output)

    def test_render_success_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            fake_result = RenderResult(plan=[], output_path=base / "output" / "ep001.mp4", dry_run=False)
            with mock.patch("bokurobo.cli.render_episode", return_value=fake_result):
                exit_code = main(["render", str(manifest_path), "--base-dir", str(base)])
            self.assertEqual(exit_code, 0)

    def test_render_error_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest_path = _write_valid_manifest(base)
            with mock.patch("bokurobo.cli.render_episode", side_effect=RenderError("boom")):
                exit_code = main(["render", str(manifest_path), "--base-dir", str(base)])
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
