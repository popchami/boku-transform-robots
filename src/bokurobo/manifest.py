"""episode.json の読み込みと検証。

標準ライブラリのみに依存する。ffprobeは実行時に見つかれば使う任意の外部
コマンドであり、未導入でも validate は動作し、該当チェックは警告に留める。

読み込み(load_manifest)は型・構造の正しさを保証し、不正な入力は
ManifestError を送出する。検証(validate_episode)はファイル存在・
パストラバーサル・尺・命名規則などの業務ルールを Issue のリストとして返す
（例外は投げない）。
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_SCENE_IDS = ("intro", "before", "transform", "after", "punchline")
ALLOWED_CATEGORIES = ("animal", "plant", "building", "food", "daily_goods")
ALLOWED_TRANSITIONS = ("cut", "zoom")
ALLOWED_VIDEO_EXTENSIONS = (".mp4",)
MIN_TOTAL_DURATION_SEC = 15.0
MAX_TOTAL_DURATION_SEC = 20.0
NARRATION_DURATION_TOLERANCE_SEC = 1.0
BANNED_WORDS = ("トランスフォーマー", "transformer", "Transformers")
FFMPEG_TEXT_SPECIAL_CHARS = set(":'\\%[],;")
OUTPUT_EXTENSION = ".mp4"


class ManifestError(ValueError):
    """episode.json を Episode に変換できないときに送出する。"""


@dataclass
class Issue:
    level: str  # "error" または "warning"
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.message}"


@dataclass
class Scene:
    id: str
    duration_sec: float
    image: str | None = None
    video: str | None = None
    narration_audio: str | None = None
    captions: list[str] = field(default_factory=list)
    sfx: list[str] = field(default_factory=list)
    transition: str = "cut"


@dataclass
class Episode:
    episode_id: str
    title: str
    category: str
    output: str
    scenes: list[Scene]
    font: str | None = None


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{field_name} は文字列である必要があります: {value!r}")
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field_name)


def _require_finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"{field_name} は数値である必要があります: {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ManifestError(f"{field_name} は有限の数値である必要があります（NaN/Infinity不可）: {value!r}")
    return number


def _require_str_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{field_name} は文字列のリストである必要があります: {value!r}")
    return value


def load_manifest(path: Path) -> Episode:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"マニフェストを読み込めません: {path} ({exc})") from exc

    try:
        data: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"JSONとして解析できません: {path} ({exc})") from exc

    if not isinstance(data, dict):
        raise ManifestError(f"マニフェストのトップレベルはオブジェクトである必要があります: {path}")

    missing = [key for key in ("episode_id", "title", "category", "output", "scenes") if key not in data]
    if missing:
        raise ManifestError(f"必須フィールドがありません: {missing} ({path})")

    scenes_raw = data["scenes"]
    if not isinstance(scenes_raw, list):
        raise ManifestError(f"'scenes' はリストである必要があります: {path}")

    scenes: list[Scene] = []
    for index, raw_scene in enumerate(scenes_raw):
        if not isinstance(raw_scene, dict):
            raise ManifestError(f"scenes[{index}] はオブジェクトである必要があります: {raw_scene!r}")
        if "id" not in raw_scene or "duration_sec" not in raw_scene:
            raise ManifestError(f"scenes[{index}] に id/duration_sec がありません: {raw_scene!r}")
        scenes.append(
            Scene(
                id=_require_str(raw_scene["id"], f"scenes[{index}].id"),
                duration_sec=_require_finite_number(raw_scene["duration_sec"], f"scenes[{index}].duration_sec"),
                image=_optional_str(raw_scene.get("image"), f"scenes[{index}].image"),
                video=_optional_str(raw_scene.get("video"), f"scenes[{index}].video"),
                narration_audio=_optional_str(
                    raw_scene.get("narration_audio"), f"scenes[{index}].narration_audio"
                ),
                captions=_require_str_list(raw_scene.get("captions"), f"scenes[{index}].captions"),
                sfx=_require_str_list(raw_scene.get("sfx"), f"scenes[{index}].sfx"),
                transition=_require_str(raw_scene.get("transition", "cut"), f"scenes[{index}].transition"),
            )
        )

    return Episode(
        episode_id=_require_str(data["episode_id"], "episode_id"),
        title=_require_str(data["title"], "title"),
        category=_require_str(data["category"], "category"),
        output=_require_str(data["output"], "output"),
        scenes=scenes,
        font=_optional_str(data.get("font"), "font"),
    )


def _check_ffmpeg_text(text: str, where: str) -> list[Issue]:
    if any(ch in FFMPEG_TEXT_SPECIAL_CHARS for ch in text):
        return [
            Issue(
                "warning",
                f"{where} にffmpeg drawtext/subtitlesでエスケープが必要な文字が含まれています（render時に要対応）: {text!r}",
            )
        ]
    return []


def _contains_banned_word(text: str) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in BANNED_WORDS)


def _resolve_within(base_dir: Path, rel_path: str, where: str) -> tuple[Path | None, Issue | None]:
    """rel_path を base_dir 配下に安全に解決する。base_dir外を指す場合はIssueを返す。"""
    candidate = Path(rel_path)
    if candidate.is_absolute():
        return None, Issue("error", f"{where} は絶対パスではなく相対パスである必要があります: {rel_path!r}")

    resolved_base = base_dir.resolve()
    resolved = (resolved_base / candidate).resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError:
        return None, Issue(
            "error", f"{where} は base_dir の外を指しています（'../'等のパストラバーサル不可）: {rel_path!r}"
        )
    return resolved, None


def _validate_output_path(base_dir: Path, output: str) -> Issue | None:
    candidate = Path(output)
    if candidate.is_absolute():
        return Issue("error", f"output は絶対パスではなく相対パスである必要があります: {output!r}")
    if candidate.suffix.lower() != OUTPUT_EXTENSION:
        return Issue("error", f"output は{OUTPUT_EXTENSION}拡張子である必要があります: {output!r}")

    output_root = (base_dir.resolve() / "output").resolve()
    resolved = (base_dir.resolve() / candidate).resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError:
        return Issue("error", f"output は 'output/' 配下でなければなりません（パストラバーサル不可）: {output!r}")
    return None


def _probe_duration_sec(path: Path) -> float | None:
    """ffprobeでメディアの尺（秒）を取得する。未導入・失敗時はNoneを返す。"""
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - shell=False, 引数は固定+パスのみ
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def validate_episode(episode: Episode, base_dir: Path) -> list[Issue]:
    """episode.json の内容を検証し、Issue のリストを返す（例外は投げない）。"""
    issues: list[Issue] = []

    if episode.category not in ALLOWED_CATEGORIES:
        issues.append(
            Issue(
                "error",
                f"category は {ALLOWED_CATEGORIES} のいずれかである必要があります: {episode.category!r}",
            )
        )

    scene_ids = [s.id for s in episode.scenes]
    if scene_ids != list(REQUIRED_SCENE_IDS):
        issues.append(
            Issue(
                "error",
                f"scenes の id は順番通り {REQUIRED_SCENE_IDS} である必要があります: {scene_ids}",
            )
        )

    total_duration = sum(s.duration_sec for s in episode.scenes)
    if not (MIN_TOTAL_DURATION_SEC <= total_duration <= MAX_TOTAL_DURATION_SEC):
        issues.append(
            Issue(
                "error",
                f"総尺は{MIN_TOTAL_DURATION_SEC}〜{MAX_TOTAL_DURATION_SEC}秒である必要がありますが{total_duration}秒です",
            )
        )

    output_issue = _validate_output_path(base_dir, episode.output)
    if output_issue:
        issues.append(output_issue)

    if _contains_banned_word(episode.title):
        issues.append(
            Issue("warning", f"title に禁止語を含む可能性があります（機械判定は補助のみ、人間レビュー必須）: {episode.title!r}")
        )

    if episode.font:
        font_path, font_issue = _resolve_within(base_dir, episode.font, "font")
        if font_issue:
            issues.append(font_issue)
        elif not font_path.is_file():
            issues.append(Issue("error", f"font が見つかりません: {episode.font}"))

    for scene in episode.scenes:
        where = f"scene[{scene.id}]"

        if scene.duration_sec <= 0:
            issues.append(
                Issue("error", f"{where}.duration_sec は正の数である必要があります: {scene.duration_sec}")
            )

        if scene.transition not in ALLOWED_TRANSITIONS:
            issues.append(
                Issue(
                    "error",
                    f"{where}.transition は {ALLOWED_TRANSITIONS} のいずれかである必要があります: {scene.transition!r}",
                )
            )

        if scene.id == "transform":
            # SPEC(2026-07-14版): transform は AI生成動画必須、他4シーンは静止画。
            # 静止画フォールバックは方針と矛盾するため設けず、video必須のerrorに統一する。
            if scene.image and scene.video:
                issues.append(
                    Issue(
                        "error",
                        f"{where} は image と video を同時に指定できません（transform は video のみを使用します）",
                    )
                )
            elif scene.video:
                video_path, video_issue = _resolve_within(base_dir, scene.video, f"{where}.video")
                if video_issue:
                    issues.append(video_issue)
                elif not video_path.is_file():
                    issues.append(Issue("error", f"{where}.video が見つかりません: {scene.video}"))
                elif video_path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
                    issues.append(
                        Issue(
                            "error",
                            f"{where}.video の拡張子は {ALLOWED_VIDEO_EXTENSIONS} のいずれかである必要があります: {scene.video!r}",
                        )
                    )
            else:
                issues.append(Issue("error", f"{where}.video が指定されていません（transform は動画必須です）"))
        else:
            if scene.video:
                issues.append(Issue("error", f"{where}.video は transform シーンでのみ指定できます"))
            if scene.image:
                image_path, image_issue = _resolve_within(base_dir, scene.image, f"{where}.image")
                if image_issue:
                    issues.append(image_issue)
                elif not image_path.is_file():
                    issues.append(Issue("error", f"{where}.image が見つかりません: {scene.image}"))
            else:
                issues.append(Issue("error", f"{where}.image が指定されていません"))

        if scene.narration_audio:
            audio_path, audio_issue = _resolve_within(base_dir, scene.narration_audio, f"{where}.narration_audio")
            if audio_issue:
                issues.append(audio_issue)
            elif not audio_path.is_file():
                issues.append(
                    Issue("error", f"{where}.narration_audio が見つかりません: {scene.narration_audio}")
                )
            else:
                duration = _probe_duration_sec(audio_path)
                if duration is None:
                    issues.append(
                        Issue(
                            "warning",
                            f"{where}.narration_audio の尺をffprobeで確認できませんでした（ffprobe未導入または解析失敗）",
                        )
                    )
                elif abs(duration - scene.duration_sec) > NARRATION_DURATION_TOLERANCE_SEC:
                    issues.append(
                        Issue(
                            "warning",
                            f"{where}.narration_audio の実尺({duration:.2f}秒)がduration_sec({scene.duration_sec}秒)"
                            f"と{NARRATION_DURATION_TOLERANCE_SEC}秒以上ずれています",
                        )
                    )

        for sfx in scene.sfx:
            sfx_path, sfx_issue = _resolve_within(base_dir, sfx, f"{where}.sfx")
            if sfx_issue:
                issues.append(sfx_issue)
            elif not sfx_path.is_file():
                issues.append(Issue("error", f"{where}.sfx が見つかりません: {sfx}"))

        for caption in scene.captions:
            issues.extend(_check_ffmpeg_text(caption, f"{where}.captions"))
            if _contains_banned_word(caption):
                issues.append(
                    Issue(
                        "warning",
                        f"{where}.captions に禁止語を含む可能性があります（機械判定は補助のみ、人間レビュー必須）: {caption!r}",
                    )
                )

    return issues
