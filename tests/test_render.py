"""bokurobo.render の単体テスト（標準ライブラリ unittest のみ使用）。

実ffmpeg/ffprobeは一切呼ばない。shutil.which と subprocess.run を全て
モックして制御フローとargv/フィルタ文字列の中身を検証する（設計方針は
docs/RENDER_DESIGN.md 4章を参照）。実ffmpegでのスモークテストは未実施であり、
別途手動での動作確認が必要。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bokurobo.manifest import Episode, Scene  # noqa: E402
from bokurobo.render import (  # noqa: E402
    CommandStep,
    CopyFileStep,
    RenderError,
    WriteFileStep,
    _check_ffmpeg_available,
    _check_ffprobe_available,
    _check_font_required,
    _escape_drawtext_text,
    _loop_warnings,
    _probe_media_info,
    build_concat_command,
    build_render_plan,
    build_scene_clip_command,
    render_episode,
)


def _which_ffmpeg_and_ffprobe(name: str) -> str | None:
    if name in ("ffmpeg", "ffprobe"):
        return f"/usr/bin/{name}"
    return None


def _fake_probe_result(stdout: str = "4.0", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _make_combined_run(*, ffmpeg_fails: bool = False, ffmpeg_stderr: str = ""):
    """bokurobo.render と bokurobo.manifest は同じ subprocess モジュールを
    参照するため、subprocess.run のモックは1箇所（同一属性）にしか効かない。
    ffprobe呼び出し（尺probe）とffmpeg呼び出し（CommandStep実行）を
    argv[0]で判別し、それぞれに適した振る舞いを返す1つのside_effectにまとめる。
    """

    def _run(argv, **kwargs):
        if argv and argv[0] == "ffprobe":
            return _fake_probe_result("4.0")
        if ffmpeg_fails:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=ffmpeg_stderr)
        Path(argv[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return _run


class EscapeDrawtextTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self) -> None:
        self.assertEqual(_escape_drawtext_text("へんしん"), "へんしん")

    def test_colon_and_comma_are_escaped(self) -> None:
        self.assertEqual(_escape_drawtext_text("a:b,c"), "a\\:b\\,c")

    def test_single_quote_is_escaped(self) -> None:
        self.assertEqual(_escape_drawtext_text("it's"), "it\\'s")

    def test_backslash_is_doubled(self) -> None:
        self.assertEqual(_escape_drawtext_text("a\\b"), "a\\\\b")

    def test_mixed_tricky_input(self) -> None:
        self.assertEqual(
            _escape_drawtext_text("コロン:カンマ,'クォート';[角括弧]"),
            "コロン\\:カンマ\\,\\'クォート\\'\\;\\[角括弧\\]",
        )


class BuildSceneClipCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmp.name)
        self.tmp_dir = self.base_dir / "tmp"
        self.tmp_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_image_scene_uses_loop_and_zoompan(self) -> None:
        scene = Scene(id="intro", duration_sec=3.0, image="image.png")
        steps = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, None)
        self.assertEqual(len(steps), 1)  # captionが無いのでWriteFileStepは無い
        step = steps[-1]
        self.assertIn("-loop", step.argv)
        self.assertIn("1", step.argv)
        filter_index = step.argv.index("-filter_complex") + 1
        self.assertIn("zoompan", step.argv[filter_index])
        self.assertIn("anullsrc", " ".join(step.argv))

    def test_zoom_transition_uses_larger_zoom_step_than_cut(self) -> None:
        cut_scene = Scene(id="intro", duration_sec=3.0, image="image.png", transition="cut")
        zoom_scene = Scene(id="intro", duration_sec=3.0, image="image.png", transition="zoom")
        cut_step = build_scene_clip_command(cut_scene, self.base_dir, self.tmp_dir, {}, None)[-1]
        zoom_step = build_scene_clip_command(zoom_scene, self.base_dir, self.tmp_dir, {}, None)[-1]
        cut_filter = cut_step.argv[cut_step.argv.index("-filter_complex") + 1]
        zoom_filter = zoom_step.argv[zoom_step.argv.index("-filter_complex") + 1]
        self.assertNotEqual(cut_filter, zoom_filter)

    def test_transform_scene_loops_when_actual_duration_is_shorter(self) -> None:
        scene = Scene(id="transform", duration_sec=4.0, video="transform.mp4")
        step = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {"transform": 2.0}, None)[-1]
        self.assertIn("-stream_loop", step.argv)
        self.assertIn("-1", step.argv)

    def test_transform_scene_does_not_loop_when_actual_duration_is_longer(self) -> None:
        scene = Scene(id="transform", duration_sec=4.0, video="transform.mp4")
        step = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {"transform": 10.0}, None)[-1]
        self.assertNotIn("-stream_loop", step.argv)

    def test_narration_only_audio_uses_anull_passthrough(self) -> None:
        scene = Scene(id="before", duration_sec=3.0, image="image.png", narration_audio="a.wav")
        step = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, None)[-1]
        filter_str = step.argv[step.argv.index("-filter_complex") + 1]
        self.assertIn("atrim", filter_str)
        self.assertIn("apad", filter_str)
        self.assertIn("anull[aout]", filter_str)
        self.assertNotIn("amix", filter_str)

    def test_narration_and_sfx_are_mixed(self) -> None:
        scene = Scene(
            id="before", duration_sec=3.0, image="image.png", narration_audio="a.wav", sfx=["s1.wav", "s2.wav"]
        )
        step = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, None)[-1]
        filter_str = step.argv[step.argv.index("-filter_complex") + 1]
        self.assertIn("amix=inputs=3:duration=longest:normalize=0[aout]", filter_str)

    def test_no_audio_source_generates_anullsrc(self) -> None:
        scene = Scene(id="before", duration_sec=3.0, image="image.png")
        step = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, None)[-1]
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", " ".join(step.argv))

    def test_captions_write_textfile_steps_and_reference_them_in_filter(self) -> None:
        scene = Scene(id="intro", duration_sec=4.0, image="image.png", captions=["A", "B"])
        safe_font_path = self.tmp_dir / "font.ttf"
        steps = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, safe_font_path)
        # 2 caption分のWriteFileStep + 最後にCommandStep
        self.assertEqual(len(steps), 3)
        caption_steps = steps[:-1]
        command_step = steps[-1]
        self.assertTrue(all(isinstance(s, WriteFileStep) for s in caption_steps))
        self.assertEqual(caption_steps[0].content, "A")  # 末尾改行を追加しない
        self.assertEqual(caption_steps[1].content, "B")
        filter_str = command_step.argv[command_step.argv.index("-filter_complex") + 1]
        self.assertEqual(filter_str.count("drawtext="), 2)
        self.assertEqual(filter_str.count("textfile="), 2)
        # caption本文はtextfile=経由でのみ渡し、text='...'埋め込みは行わないこと
        self.assertNotIn("text='", filter_str)
        for caption_step in caption_steps:
            self.assertIn(f"textfile='{caption_step.path}'", filter_str)
        self.assertIn("between(t,0.000,2.000)", filter_str)
        self.assertIn("between(t,2.000,4.000)", filter_str)

    def test_no_captions_means_no_drawtext_and_no_write_steps(self) -> None:
        scene = Scene(id="intro", duration_sec=3.0, image="image.png")
        steps = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, None)
        self.assertEqual(len(steps), 1)
        filter_str = steps[-1].argv[steps[-1].argv.index("-filter_complex") + 1]
        self.assertNotIn("drawtext", filter_str)

    def test_caption_special_chars_are_written_verbatim_to_textfile(self) -> None:
        # textfile方式ではcaption本文はファイル内容としてそのまま書かれ、
        # フィルタ文字列側のエスケープ対象にはならない（3.5参照）。
        scene = Scene(id="intro", duration_sec=3.0, image="image.png", captions=["a:b,'c"])
        steps = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, self.tmp_dir / "font.ttf")
        caption_step = steps[0]
        self.assertIsInstance(caption_step, WriteFileStep)
        self.assertEqual(caption_step.content, "a:b,'c")

    def test_font_path_special_chars_are_escaped_in_filter(self) -> None:
        scene = Scene(id="intro", duration_sec=3.0, image="image.png", captions=["hi"])
        tricky_font_path = self.tmp_dir / "weird:name'.ttf"
        steps = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, tricky_font_path)
        command_step = steps[-1]
        filter_str = command_step.argv[command_step.argv.index("-filter_complex") + 1]
        self.assertIn("fontfile='" + _escape_drawtext_text(str(tricky_font_path)) + "'", filter_str)
        self.assertNotIn(f"fontfile='{tricky_font_path}'", filter_str)

    def test_common_encode_settings_are_present(self) -> None:
        scene = Scene(id="intro", duration_sec=3.0, image="image.png")
        step = build_scene_clip_command(scene, self.base_dir, self.tmp_dir, {}, None)[-1]
        for expected in (
            "-r", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-profile:v", "high",
            "-level:v", "4.0", "-video_track_timescale", "30000",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
        ):
            self.assertIn(expected, step.argv)


class BuildConcatCommandTests(unittest.TestCase):
    def test_write_step_and_command_step_are_returned(self) -> None:
        tmp_dir = Path("/tmp/x")
        clip_paths = [tmp_dir / "a.mp4", tmp_dir / "b.mp4"]
        steps = build_concat_command(clip_paths, tmp_dir, tmp_dir / "final.mp4")
        self.assertEqual(len(steps), 2)
        self.assertIsInstance(steps[0], WriteFileStep)
        self.assertIsInstance(steps[1], CommandStep)
        self.assertEqual(
            steps[1].argv,
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(tmp_dir / "concat_list.txt"), "-c", "copy", str(tmp_dir / "final.mp4")],
        )

    def test_single_quote_in_path_is_escaped(self) -> None:
        tmp_dir = Path("/tmp/x")
        clip_paths = [tmp_dir / "it's.mp4"]
        steps = build_concat_command(clip_paths, tmp_dir, tmp_dir / "final.mp4")
        write_step = steps[0]
        assert isinstance(write_step, WriteFileStep)
        self.assertIn("it'\\''s.mp4", write_step.content)


class BuildRenderPlanTests(unittest.TestCase):
    def test_plan_has_one_command_per_scene_plus_concat(self) -> None:
        episode = Episode(
            episode_id="ep001",
            title="t",
            category="daily_goods",
            output="output/ep001.mp4",
            scenes=[
                Scene(id="intro", duration_sec=3.0, image="image.png"),
                Scene(id="before", duration_sec=3.0, image="image.png"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            plan = build_render_plan(episode, base_dir, base_dir, {})
            # 2シーン分のCommandStep + concatのWriteFileStep/CommandStep
            self.assertEqual(len(plan), 4)
            self.assertIsInstance(plan[-1], CommandStep)
            self.assertIsInstance(plan[-2], WriteFileStep)

    def test_font_is_copied_to_safe_path_and_referenced_by_scenes(self) -> None:
        episode = Episode(
            episode_id="ep001", title="t", category="daily_goods", output="output/ep001.mp4",
            font="fonts/weird:name'.ttf",
            scenes=[Scene(id="intro", duration_sec=3.0, image="image.png", captions=["hi"])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "fonts").mkdir()
            (base_dir / "fonts" / "weird:name'.ttf").touch()
            tmp_dir = base_dir / "work"
            tmp_dir.mkdir()
            plan = build_render_plan(episode, base_dir, tmp_dir, {})
            copy_step = plan[0]
            self.assertIsInstance(copy_step, CopyFileStep)
            self.assertEqual(copy_step.src, base_dir / "fonts" / "weird:name'.ttf")
            self.assertEqual(copy_step.dst, tmp_dir / "font.ttf")
            # captionを含むシーンのCommandStepが、コピー先の安全パスだけを参照している
            # plan = [CopyFileStep(font), WriteFileStep(caption), CommandStep(scene), WriteFileStep(concat), CommandStep(concat)]
            command_step = plan[2]
            self.assertIsInstance(command_step, CommandStep)
            filter_str = command_step.argv[command_step.argv.index("-filter_complex") + 1]
            self.assertIn(_escape_drawtext_text(str(copy_step.dst)), filter_str)
            self.assertNotIn(str(copy_step.src), filter_str)


class PreflightTests(unittest.TestCase):
    def test_font_required_raises_when_caption_present_and_font_missing(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="intro", duration_sec=3.0, image="i.png", captions=["hi"])],
        )
        with self.assertRaises(RenderError):
            _check_font_required(episode)

    def test_font_required_passes_when_no_captions(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="intro", duration_sec=3.0, image="i.png")],
        )
        _check_font_required(episode)  # 例外が出なければOK

    def test_ffprobe_required_when_video_scene_present(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="transform", duration_sec=4.0, video="v.mp4")],
        )
        with mock.patch("bokurobo.render.shutil.which", return_value=None):
            with self.assertRaises(RenderError):
                _check_ffprobe_available(episode)

    def test_ffprobe_not_required_without_video_scene(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="intro", duration_sec=3.0, image="i.png")],
        )
        with mock.patch("bokurobo.render.shutil.which", return_value=None):
            _check_ffprobe_available(episode)  # 例外が出なければOK

    def test_ffmpeg_required(self) -> None:
        with mock.patch("bokurobo.render.shutil.which", return_value=None):
            with self.assertRaises(RenderError):
                _check_ffmpeg_available()

    def test_probe_media_info_collects_transform_duration(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="transform", duration_sec=4.0, video="v.mp4")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "v.mp4").touch()
            with mock.patch("bokurobo.manifest.shutil.which", return_value="/usr/bin/ffprobe"), mock.patch(
                "bokurobo.manifest.subprocess.run", return_value=_fake_probe_result("4.0")
            ):
                media_info = _probe_media_info(episode, base_dir)
        self.assertEqual(media_info, {"transform": 4.0})

    def test_loop_warning_emitted_when_actual_duration_shorter(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="transform", duration_sec=4.0, video="v.mp4")],
        )
        warnings = _loop_warnings(episode, {"transform": 2.0})
        self.assertEqual(len(warnings), 1)
        self.assertIn("transform", warnings[0])
        self.assertIn("ループ", warnings[0])

    def test_no_loop_warning_when_actual_duration_is_enough(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="transform", duration_sec=4.0, video="v.mp4")],
        )
        self.assertEqual(_loop_warnings(episode, {"transform": 4.0}), [])
        self.assertEqual(_loop_warnings(episode, {"transform": 10.0}), [])

    def test_probe_media_info_raises_when_duration_unparseable(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
            scenes=[Scene(id="transform", duration_sec=4.0, video="v.mp4")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "v.mp4").touch()
            with mock.patch("bokurobo.manifest.shutil.which", return_value="/usr/bin/ffprobe"), mock.patch(
                "bokurobo.manifest.subprocess.run", return_value=_fake_probe_result("not-a-number")
            ):
                with self.assertRaises(RenderError):
                    _probe_media_info(episode, base_dir)


def _which_ffprobe_only(name: str) -> str | None:
    if name == "ffprobe":
        return "/usr/bin/ffprobe"
    return None


def _valid_episode_minimal() -> Episode:
    # REQUIRED_SCENE_IDSは5シーン固定・total durationは15-20秒必須なので、
    # transform/after にはvideoが必須（image+videoの併用は不可、SPEC 2026-07-15版）。
    return Episode(
        episode_id="e", title="t", category="daily_goods", output="output/o.mp4",
        scenes=[
            Scene(id="intro", duration_sec=3.0, image="i1.png"),
            Scene(id="before", duration_sec=3.0, image="i2.png"),
            Scene(id="transform", duration_sec=4.0, video="v.mp4"),
            Scene(id="after", duration_sec=6.0, video="v_after.mp4"),
            Scene(id="punchline", duration_sec=4.0, image="i4.png"),
        ],
    )


class RenderEpisodeControlFlowTests(unittest.TestCase):
    """render_episode の制御フロー（preflight/dry-run境界/失敗時temp保持/成功時cleanup）を
    subprocess.run と shutil.which をモックして検証する。実ffmpegは呼ばない。"""

    def _make_base_dir(self, tmp: str, episode: Episode) -> Path:
        base_dir = Path(tmp)
        for scene in episode.scenes:
            if scene.image:
                (base_dir / scene.image).touch()
            if scene.video:
                (base_dir / scene.video).touch()
        (base_dir / "output").mkdir(exist_ok=True)
        return base_dir

    def test_validate_error_blocks_render_without_touching_ffmpeg(self) -> None:
        episode = Episode(
            episode_id="e", title="t", category="vehicle", output="output/o.mp4",  # 不正なcategory
            scenes=[Scene(id="intro", duration_sec=3.0, image="i1.png")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)
            with mock.patch("bokurobo.render.subprocess.run") as mock_run:
                with self.assertRaises(RenderError):
                    render_episode(episode, base_dir, dry_run=True)
                mock_run.assert_not_called()

    def test_result_carries_loop_warning_when_transform_video_is_short(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)

            def short_probe_run(argv, **kwargs):
                if argv and argv[0] == "ffprobe":
                    # transform(v.mp4)のみ短尺(duration_sec=4.0より短い2.0秒)を返し、
                    # after(v_after.mp4)は declared duration 通りの尺を返して警告対象から外す。
                    if str(argv[-1]).endswith("v.mp4"):
                        return _fake_probe_result("2.0")
                    return _fake_probe_result("6.0")
                Path(argv[-1]).write_bytes(b"fake-mp4")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            with mock.patch("bokurobo.render.subprocess.run", side_effect=short_probe_run), mock.patch(
                "bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe
            ):
                result = render_episode(episode, base_dir, dry_run=True)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("transform", result.warnings[0])

    def test_dry_run_builds_plan_without_calling_ffmpeg(self) -> None:
        # bokurobo.render と bokurobo.manifest は同じsubprocessモジュールを参照するため
        # （_make_combined_runのdocstring参照）、パッチ対象は1箇所（render側）だけでよい。
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)
            with mock.patch(
                "bokurobo.render.subprocess.run", side_effect=_make_combined_run()
            ) as mock_run, mock.patch("bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe):
                result = render_episode(episode, base_dir, dry_run=True)
            ffmpeg_calls = [call.args[0] for call in mock_run.call_args_list if call.args[0][0] == "ffmpeg"]
            self.assertEqual(ffmpeg_calls, [], "dry_runではffmpeg変換コマンドを呼ばないこと")
        self.assertTrue(result.dry_run)
        self.assertEqual(len(result.plan), 7)  # 5 scenes + concatのwrite/command

    def test_success_moves_output_and_cleans_up_tempdir(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)

            captured_tmp_dir: list[Path] = []
            real_mkdtemp = tempfile.mkdtemp

            def spy_mkdtemp(*args, **kwargs):
                created = real_mkdtemp(*args, **kwargs)
                captured_tmp_dir.append(Path(created))
                return created

            with mock.patch(
                "bokurobo.render.subprocess.run", side_effect=_make_combined_run()
            ), mock.patch(
                "bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe
            ), mock.patch("bokurobo.render.tempfile.mkdtemp", side_effect=spy_mkdtemp):
                result = render_episode(episode, base_dir, dry_run=False)

            self.assertFalse(result.dry_run)
            self.assertTrue(result.output_path.is_file())
            self.assertEqual(len(captured_tmp_dir), 1)
            self.assertFalse(captured_tmp_dir[0].exists(), "成功時はtempdirが削除されているべき")

    def test_failure_keeps_tempdir_and_includes_path_and_stderr_in_error(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)

            captured_tmp_dir: list[Path] = []
            real_mkdtemp = tempfile.mkdtemp

            def spy_mkdtemp(*args, **kwargs):
                created = real_mkdtemp(*args, **kwargs)
                captured_tmp_dir.append(Path(created))
                return created

            with mock.patch(
                "bokurobo.render.subprocess.run",
                side_effect=_make_combined_run(ffmpeg_fails=True, ffmpeg_stderr="dummy ffmpeg failure detail"),
            ), mock.patch(
                "bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe
            ), mock.patch("bokurobo.render.tempfile.mkdtemp", side_effect=spy_mkdtemp):
                with self.assertRaises(RenderError) as cm:
                    render_episode(episode, base_dir, dry_run=False)

            self.assertEqual(len(captured_tmp_dir), 1)
            self.assertTrue(captured_tmp_dir[0].exists(), "失敗時はtempdirを保持するべき")
            self.assertIn(str(captured_tmp_dir[0]), str(cm.exception))
            self.assertIn("dummy ffmpeg failure detail", str(cm.exception))
            # 後始末: このテストのみ手動でtempdirを削除する
            shutil.rmtree(captured_tmp_dir[0], ignore_errors=True)

    def test_unexpected_oserror_during_execution_is_converted_and_keeps_tempdir(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)

            def crashing_run(argv, **kwargs):
                if argv and argv[0] == "ffprobe":
                    return _fake_probe_result("4.0")
                raise OSError("ffmpegバイナリの起動に失敗（想定外のOSError）")

            captured_tmp_dir: list[Path] = []
            real_mkdtemp = tempfile.mkdtemp

            def spy_mkdtemp(*args, **kwargs):
                created = real_mkdtemp(*args, **kwargs)
                captured_tmp_dir.append(Path(created))
                return created

            with mock.patch("bokurobo.render.subprocess.run", side_effect=crashing_run), mock.patch(
                "bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe
            ), mock.patch("bokurobo.render.tempfile.mkdtemp", side_effect=spy_mkdtemp):
                with self.assertRaises(RenderError) as cm:
                    render_episode(episode, base_dir, dry_run=False)

            self.assertEqual(len(captured_tmp_dir), 1)
            self.assertTrue(captured_tmp_dir[0].exists(), "想定外の例外でもtempdirを保持するべき")
            self.assertIn(str(captured_tmp_dir[0]), str(cm.exception))
            shutil.rmtree(captured_tmp_dir[0], ignore_errors=True)

    def test_move_failure_when_final_clip_missing_is_converted_and_keeps_tempdir(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)

            def run_without_writing_output(argv, **kwargs):
                if argv and argv[0] == "ffprobe":
                    return _fake_probe_result("4.0")
                # returncode=0で成功したように見せるが、実際にはファイルを書かない
                # （ffmpegが不整合終了する等の想定外ケースを模す）。
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            captured_tmp_dir: list[Path] = []
            real_mkdtemp = tempfile.mkdtemp

            def spy_mkdtemp(*args, **kwargs):
                created = real_mkdtemp(*args, **kwargs)
                captured_tmp_dir.append(Path(created))
                return created

            with mock.patch(
                "bokurobo.render.subprocess.run", side_effect=run_without_writing_output
            ), mock.patch(
                "bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe
            ), mock.patch("bokurobo.render.tempfile.mkdtemp", side_effect=spy_mkdtemp):
                with self.assertRaises(RenderError) as cm:
                    render_episode(episode, base_dir, dry_run=False)

            self.assertEqual(len(captured_tmp_dir), 1)
            self.assertTrue(captured_tmp_dir[0].exists(), "move失敗時もtempdirを保持するべき")
            self.assertIn(str(captured_tmp_dir[0]), str(cm.exception))
            shutil.rmtree(captured_tmp_dir[0], ignore_errors=True)

    def test_rmtree_failure_after_success_is_warning_not_error(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)

            captured_tmp_dir: list[Path] = []
            real_mkdtemp = tempfile.mkdtemp

            def spy_mkdtemp(*args, **kwargs):
                created = real_mkdtemp(*args, **kwargs)
                captured_tmp_dir.append(Path(created))
                return created

            with mock.patch(
                "bokurobo.render.subprocess.run", side_effect=_make_combined_run()
            ), mock.patch(
                "bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe
            ), mock.patch("bokurobo.render.tempfile.mkdtemp", side_effect=spy_mkdtemp), mock.patch(
                "bokurobo.render.shutil.rmtree", side_effect=OSError("削除できない想定")
            ):
                result = render_episode(episode, base_dir, dry_run=False)

            self.assertFalse(result.dry_run)
            self.assertTrue(result.output_path.is_file(), "rmtree失敗でも生成物自体は成功しているべき")
            self.assertTrue(any("一時ディレクトリの削除に失敗" in w for w in result.warnings))
            # rmtreeをモックしたため実ディレクトリが残っている。後始末する。
            shutil.rmtree(captured_tmp_dir[0], ignore_errors=True)

    def test_validate_warnings_are_passed_through_to_result(self) -> None:
        episode = _valid_episode_minimal()
        episode.title = "トランスフォーマーだいすき"  # BANNED_WORDSに一致しwarning対象
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)
            with mock.patch(
                "bokurobo.render.subprocess.run", side_effect=_make_combined_run()
            ), mock.patch("bokurobo.render.shutil.which", side_effect=_which_ffmpeg_and_ffprobe):
                result = render_episode(episode, base_dir, dry_run=True)
        self.assertTrue(any("禁止語" in w for w in result.warnings))

    def test_missing_ffmpeg_blocks_real_run_but_not_dry_run(self) -> None:
        episode = _valid_episode_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = self._make_base_dir(tmp, episode)
            with mock.patch("bokurobo.render.shutil.which", side_effect=_which_ffprobe_only), mock.patch(
                "bokurobo.manifest.shutil.which", side_effect=_which_ffprobe_only
            ), mock.patch("bokurobo.manifest.subprocess.run", return_value=_fake_probe_result("4.0")):
                result = render_episode(episode, base_dir, dry_run=True)
                self.assertTrue(result.dry_run)
                with self.assertRaises(RenderError):
                    render_episode(episode, base_dir, dry_run=False)


if __name__ == "__main__":
    unittest.main()
