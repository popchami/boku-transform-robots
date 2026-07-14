"""episode.json から動画を合成する render サブコマンドの実装。

docs/RENDER_DESIGN.md の設計に従う。シーン単位で正規化済みmp4クリップを
1本ずつffmpegで生成し、concat demuxer（-c copy）で結合する。

build_render_plan / build_scene_clip_command / build_concat_command は
純粋関数（subprocessを呼ばずargv列を組み立てるだけ）であり、実際の
ファイル書き込み・ffmpeg実行は render_episode の実行フェーズが担う。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from .manifest import (
    FFMPEG_TEXT_SPECIAL_CHARS,
    Episode,
    Scene,
    _probe_duration_sec,
    _resolve_within,
    validate_episode,
)

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_CODEC = "libx264"
VIDEO_PROFILE = "high"
VIDEO_LEVEL = "4.0"
VIDEO_PIXEL_FORMAT = "yuv420p"
VIDEO_TRACK_TIMESCALE = "30000"
AUDIO_CODEC = "aac"
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNEL_LAYOUT = "stereo"
STDERR_TAIL_CHARS = 4000

RenderMediaInfo = dict[str, float]


class RenderError(RuntimeError):
    """renderに失敗したときに送出する。"""


@dataclass
class WriteFileStep:
    path: Path
    content: str


@dataclass
class CommandStep:
    description: str
    argv: list[str]
    produces: Path


@dataclass
class CopyFileStep:
    src: Path
    dst: Path


RenderStep = Union[WriteFileStep, CommandStep, CopyFileStep]
RenderPlan = list[RenderStep]


@dataclass
class RenderResult:
    plan: RenderPlan
    output_path: Path
    dry_run: bool
    warnings: list[str] = field(default_factory=list)


def _escape_drawtext_text(text: str) -> str:
    """drawtextのtext=引数用にffmpegフィルタ文字列エスケープを行う。"""
    result: list[str] = []
    for ch in text:
        if ch in FFMPEG_TEXT_SPECIAL_CHARS:
            result.append("\\")
        result.append(ch)
    return "".join(result)


def _require_duration_sec(path: Path) -> float:
    duration = _probe_duration_sec(path)
    if duration is None:
        raise RenderError(f"ffprobeで尺を取得できませんでした: {path}")
    return duration


def _resolve_or_raise(base_dir: Path, rel_path: str, where: str) -> Path:
    resolved, issue = _resolve_within(base_dir, rel_path, where)
    if issue is not None:
        raise RenderError(str(issue))
    assert resolved is not None
    return resolved


def _resolve_font_path(base_dir: Path, font: str | None) -> Path | None:
    if font is None:
        return None
    return _resolve_or_raise(base_dir, font, "font")


def _resolve_output_path(base_dir: Path, output: str) -> Path:
    return (base_dir.resolve() / output).resolve()


def _check_font_required(episode: Episode) -> None:
    has_captions = any(scene.captions for scene in episode.scenes)
    if has_captions and not episode.font:
        raise RenderError(
            "captionを含むシーンがありますが episode.font が指定されていません"
            "（drawtextにfontfileを渡せないためrenderできません）"
        )


def _check_ffprobe_available(episode: Episode) -> None:
    has_video_scene = any(scene.video for scene in episode.scenes)
    if has_video_scene and shutil.which("ffprobe") is None:
        raise RenderError("videoシーンを含むepisodeのrenderにはffprobeが必要ですが見つかりませんでした")


def _check_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RenderError("renderにはffmpegが必要ですが見つかりませんでした")


def _loop_warnings(episode: Episode, media_info: RenderMediaInfo) -> list[str]:
    """5.2決定事項: 実尺がduration_secより短いtransformシーンはループするが、
    ループ境界で映像が不連続になりうるためwarningとして明記する。"""
    warnings: list[str] = []
    for scene in episode.scenes:
        actual_duration = media_info.get(scene.id)
        if actual_duration is not None and actual_duration < scene.duration_sec:
            warnings.append(
                f"scene[{scene.id}]: videoの実尺({actual_duration:.2f}秒)がduration_sec({scene.duration_sec}秒)"
                "より短いためループします。ループ境界で映像が不連続になる場合があるので目視確認してください"
            )
    return warnings


def _probe_media_info(episode: Episode, base_dir: Path) -> RenderMediaInfo:
    media_info: RenderMediaInfo = {}
    for scene in episode.scenes:
        if scene.video:
            video_path = _resolve_or_raise(base_dir, scene.video, f"scene[{scene.id}].video")
            media_info[scene.id] = _require_duration_sec(video_path)
    return media_info


def _caption_segments(scene: Scene) -> list[tuple[str, float, float]]:
    """5.1決定事項: duration_secをcaption数で均等分割し、順番に表示する。"""
    count = len(scene.captions)
    if count == 0:
        return []
    segment = scene.duration_sec / count
    return [(caption, i * segment, (i + 1) * segment) for i, caption in enumerate(scene.captions)]


def _build_video_filter(scene: Scene, media_info: RenderMediaInfo) -> str:
    if scene.video:
        # transformシーン: scale+cropで正規化、fpsを統一する（3.1）。
        return (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS}"
        )
    # 静止画シーン: zoompanでKen Burns風の演出をつけつつ正規化する（3.1）。
    frames = round(scene.duration_sec * VIDEO_FPS)
    zoom_expr = "min(zoom+0.0020,1.5)" if scene.transition == "zoom" else "min(zoom+0.0008,1.15)"
    return (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        f"zoompan=z='{zoom_expr}':d={frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS}"
    )


def _build_caption_filters(
    scene: Scene, tmp_dir: Path, safe_font_path: Path | None
) -> tuple[list[WriteFileStep], str]:
    """captionは実FFmpegのフィルタ文字列内エスケープが多段かつ不安定なため、
    text=への埋め込みではなくtmp_dir内の安全な固定名ファイルに書き出し、
    drawtextのtextfile=で読ませる（fontfileも同様にコピー済みの安全パスのみ使う）。
    フィルタ文字列に載せるのはtmp_dir配下の制御可能なパスのみだが、念のため
    そのパス自体もフィルタエスケープする。"""
    write_steps: list[WriteFileStep] = []
    filter_parts: list[str] = []
    escaped_font_path = _escape_drawtext_text(str(safe_font_path)) if safe_font_path is not None else ""
    for index, (caption, start, end) in enumerate(_caption_segments(scene)):
        caption_path = tmp_dir / f"{scene.id}_caption_{index}.txt"
        write_steps.append(WriteFileStep(path=caption_path, content=caption))
        escaped_caption_path = _escape_drawtext_text(str(caption_path))
        filter_parts.append(
            "drawtext="
            f"fontfile='{escaped_font_path}':textfile='{escaped_caption_path}':"
            "x=(w-text_w)/2:y=h-200:fontsize=64:fontcolor=white:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    filter_str = "".join(f",{part}" for part in filter_parts)
    return write_steps, filter_str


def build_scene_clip_command(
    scene: Scene,
    base_dir: Path,
    tmp_dir: Path,
    media_info: RenderMediaInfo,
    safe_font_path: Path | None,
) -> list[RenderStep]:
    duration = scene.duration_sec
    visual_input_argv: list[str]

    if scene.video:
        video_path = _resolve_or_raise(base_dir, scene.video, f"scene[{scene.id}].video")
        actual_duration = media_info[scene.id]
        loop_needed = actual_duration < duration
        visual_input_argv = (["-stream_loop", "-1"] if loop_needed else []) + ["-i", str(video_path)]
    else:
        assert scene.image is not None
        image_path = _resolve_or_raise(base_dir, scene.image, f"scene[{scene.id}].image")
        visual_input_argv = ["-loop", "1", "-i", str(image_path)]

    audio_input_argv: list[str] = []
    audio_labels: list[str] = []
    next_index = 1

    if scene.narration_audio:
        narration_path = _resolve_or_raise(base_dir, scene.narration_audio, f"scene[{scene.id}].narration_audio")
        audio_input_argv += ["-i", str(narration_path)]
        audio_labels.append(f"[{next_index}:a]atrim=0:{duration},asetpts=PTS-STARTPTS,apad=whole_dur={duration}[a{next_index}]")
        next_index += 1

    for sfx in scene.sfx:
        sfx_path = _resolve_or_raise(base_dir, sfx, f"scene[{scene.id}].sfx")
        audio_input_argv += ["-i", str(sfx_path)]
        audio_labels.append(f"[{next_index}:a]atrim=0:{duration},asetpts=PTS-STARTPTS,apad=whole_dur={duration}[a{next_index}]")
        next_index += 1

    if not audio_labels:
        audio_input_argv += [
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:sample_rate={AUDIO_SAMPLE_RATE}",
        ]
        audio_labels.append(f"[{next_index}:a]atrim=0:{duration},asetpts=PTS-STARTPTS[a{next_index}]")
        next_index += 1

    mix_labels = "".join(f"[a{i}]" for i in range(1, next_index))
    if next_index - 1 == 1:
        audio_filter = f"{audio_labels[0]};{mix_labels}anull[aout]"
    else:
        # 5.3決定事項: 各sfx/narrationは0dB（無加工）でmixする。amixのデフォルト
        # normalize=1は入力数に応じて自動減衰させてしまい「無加工」と矛盾するため
        # normalize=0を明示する（クリッピングが起きうるため生成物は目視・聴取確認前提）。
        audio_filter = (
            ";".join(audio_labels)
            + f";{mix_labels}amix=inputs={next_index - 1}:duration=longest:normalize=0[aout]"
        )

    caption_write_steps, caption_filter = _build_caption_filters(scene, tmp_dir, safe_font_path)
    video_filter = _build_video_filter(scene, media_info) + caption_filter
    filter_complex = f"[0:v]{video_filter}[vout];{audio_filter}"

    clip_path = tmp_dir / f"{scene.id}.mp4"
    argv = (
        ["ffmpeg"]
        + visual_input_argv
        + audio_input_argv
        + [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-t", str(duration),
            "-r", str(VIDEO_FPS),
            "-pix_fmt", VIDEO_PIXEL_FORMAT,
            "-c:v", VIDEO_CODEC,
            "-profile:v", VIDEO_PROFILE,
            "-level:v", VIDEO_LEVEL,
            "-video_track_timescale", VIDEO_TRACK_TIMESCALE,
            "-c:a", AUDIO_CODEC,
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", "2",
            str(clip_path),
        ]
    )
    command_step = CommandStep(description=f"scene[{scene.id}] clip", argv=argv, produces=clip_path)
    return [*caption_write_steps, command_step]


def _escape_concat_path(path: Path) -> str:
    # concat demuxerのlistファイル内でシングルクォートを閉じて再開する定石のエスケープ。
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'"


def build_concat_command(clip_paths: list[Path], tmp_dir: Path, final_path: Path) -> list[RenderStep]:
    lines = [_escape_concat_path(path) for path in clip_paths]
    content = "\n".join(lines) + "\n"
    list_path = tmp_dir / "concat_list.txt"
    write_step = WriteFileStep(path=list_path, content=content)
    command_step = CommandStep(
        description="concat",
        argv=["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(final_path)],
        produces=final_path,
    )
    return [write_step, command_step]


def build_render_plan(episode: Episode, base_dir: Path, tmp_dir: Path, media_info: RenderMediaInfo) -> RenderPlan:
    font_path = _resolve_font_path(base_dir, episode.font)
    plan: RenderPlan = []

    # fontfile=に渡すのは元のfont_path（任意の場所・エスケープ困難な文字を含みうる）
    # ではなく、tmp_dir内へコピーした安全な固定名パスのみとする（P1修正）。
    safe_font_path: Path | None = None
    if font_path is not None:
        safe_font_path = tmp_dir / f"font{font_path.suffix}"
        plan.append(CopyFileStep(src=font_path, dst=safe_font_path))

    clip_paths: list[Path] = []
    for scene in episode.scenes:
        steps = build_scene_clip_command(scene, base_dir, tmp_dir, media_info, safe_font_path)
        plan.extend(steps)
        clip_paths.append(steps[-1].produces)
    plan.extend(build_concat_command(clip_paths, tmp_dir, tmp_dir / "final.mp4"))
    return plan


def _execute_plan(plan: RenderPlan) -> None:
    for step in plan:
        if isinstance(step, WriteFileStep):
            step.path.write_text(step.content, encoding="utf-8")
        elif isinstance(step, CopyFileStep):
            shutil.copy2(step.src, step.dst)
        else:
            result = subprocess.run(step.argv, capture_output=True, text=True, shell=False, check=False)
            if result.returncode != 0:
                stderr_tail = result.stderr[-STDERR_TAIL_CHARS:]
                raise RenderError(
                    f"{step.description} が失敗しました (exit={result.returncode}): {stderr_tail}"
                )


def render_episode(episode: Episode, base_dir: Path, *, dry_run: bool = False) -> RenderResult:
    issues = validate_episode(episode, base_dir)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        joined = "; ".join(str(issue) for issue in errors)
        raise RenderError(f"validate_episodeでエラーが検出されたためrenderを中断しました: {joined}")

    # 3.7: warningは中断せずrender結果に含める（既存のvalidate警告 + 5.2のloop警告）。
    warnings = [str(issue) for issue in issues if issue.level == "warning"]

    _check_font_required(episode)
    _check_ffprobe_available(episode)
    media_info = _probe_media_info(episode, base_dir)
    output_path = _resolve_output_path(base_dir, episode.output)
    warnings += _loop_warnings(episode, media_info)

    if dry_run:
        plan = build_render_plan(episode, base_dir, Path("<tmp>"), media_info)
        return RenderResult(plan=plan, output_path=output_path, dry_run=True, warnings=warnings)

    _check_ffmpeg_available()
    tmp_dir = Path(tempfile.mkdtemp(prefix="bokurobo_render_"))
    try:
        plan = build_render_plan(episode, base_dir, tmp_dir, media_info)
        _execute_plan(plan)
        final_clip = plan[-1].produces
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final_clip), str(output_path))
    except RenderError as exc:
        raise RenderError(f"{exc} (中間ファイルはtempdirに保持されています: {tmp_dir})") from exc
    except (OSError, subprocess.SubprocessError, KeyError) as exc:
        raise RenderError(
            f"renderの実行中に予期しないエラーが発生しました: {exc!r} "
            f"(中間ファイルはtempdirに保持されています: {tmp_dir})"
        ) from exc

    # ここまで到達した時点でoutput_pathへの生成は成功済み。tempdir削除の失敗は
    # render自体の失敗として扱わず、warningとして通知するに留める。
    try:
        shutil.rmtree(tmp_dir)
    except OSError as exc:
        warnings.append(f"一時ディレクトリの削除に失敗しました（生成物自体は正常です）: {tmp_dir} ({exc})")

    return RenderResult(plan=plan, output_path=output_path, dry_run=False, warnings=warnings)
