"""episode.json の読み込みと検証。

標準ライブラリのみに依存する。ffprobeは実行時に見つかれば使う任意の外部
コマンドであり、未導入でも validate は動作し、該当チェックは警告に留める。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_SCENE_IDS = ("intro", "before", "transform", "after", "punchline")
ALLOWED_CATEGORIES = ("animal", "plant", "building", "food", "daily_goods")
MIN_TOTAL_DURATION_SEC = 15.0
MAX_TOTAL_DURATION_SEC = 20.0
NARRATION_DURATION_TOLERANCE_SEC = 1.0
BANNED_WORDS = ("トランスフォーマー", "transformer", "Transformers")
FFMPEG_TEXT_SPECIAL_CHARS = set(":'\\%[],;")


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

    try:
        scenes_raw = data["scenes"]
        if not isinstance(scenes_raw, list):
            raise ManifestError(f"'scenes' はリストである必要があります: {path}")
        scenes = [
            Scene(
                id=s["id"],
                duration_sec=float(s["duration_sec"]),
                image=s.get("image"),
                narration_audio=s.get("narration_audio"),
                captions=list(s.get("captions", [])),
                sfx=list(s.get("sfx", [])),
                transition=s.get("transition", "cut"),
            )
            for s in scenes_raw
        ]
        episode = Episode(
            episode_id=data["episode_id"],
            title=data["title"],
            category=data["category"],
            output=data["output"],
            scenes=scenes,
            font=data.get("font"),
        )
    except KeyError as exc:
        raise ManifestError(f"必須フィールドがありません: {exc} ({path})") from exc
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"マニフェストの形式が不正です: {exc} ({path})") from exc

    return episode


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


def _resolve(base_dir: Path, rel_path: str) -> Path:
    return (base_dir / rel_path).resolve()


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

    output_path = Path(episode.output)
    if output_path.is_absolute() or output_path.parts[:1] != ("output",):
        issues.append(
            Issue("error", f"output は 'output/' 配下の相対パスである必要があります: {episode.output!r}")
        )

    if _contains_banned_word(episode.title):
        issues.append(
            Issue("warning", f"title に禁止語を含む可能性があります（機械判定は補助のみ、人間レビュー必須）: {episode.title!r}")
        )

    if episode.font:
        font_path = _resolve(base_dir, episode.font)
        if not font_path.is_file():
            issues.append(Issue("error", f"font が見つかりません: {episode.font}"))

    for scene in episode.scenes:
        where = f"scene[{scene.id}]"

        if scene.duration_sec <= 0:
            issues.append(
                Issue("error", f"{where}.duration_sec は正の数である必要があります: {scene.duration_sec}")
            )

        if scene.image:
            image_path = _resolve(base_dir, scene.image)
            if not image_path.is_file():
                issues.append(Issue("error", f"{where}.image が見つかりません: {scene.image}"))
        else:
            issues.append(Issue("error", f"{where}.image が指定されていません"))

        if scene.narration_audio:
            audio_path = _resolve(base_dir, scene.narration_audio)
            if not audio_path.is_file():
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
            sfx_path = _resolve(base_dir, sfx)
            if not sfx_path.is_file():
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
