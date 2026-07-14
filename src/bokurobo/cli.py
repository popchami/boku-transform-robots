"""bokurobo CLI エントリポイント。

使い方:
    python -m bokurobo.cli validate episodes/<話数>/episode.json
    python -m bokurobo.cli render episodes/<話数>/episode.json [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .manifest import ManifestError, load_manifest, validate_episode
from .render import CommandStep, CopyFileStep, RenderError, WriteFileStep, render_episode

REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_validate(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    try:
        episode = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    base_dir = args.base_dir.resolve() if args.base_dir else REPO_ROOT
    issues = validate_episode(episode, base_dir)

    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]

    for issue in issues:
        print(issue)

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def cmd_render(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    try:
        episode = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    base_dir = args.base_dir.resolve() if args.base_dir else REPO_ROOT
    try:
        result = render_episode(episode, base_dir, dry_run=args.dry_run)
    except RenderError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    for warning in result.warnings:
        print(f"[WARNING] {warning}", file=sys.stderr)

    if result.dry_run:
        print(f"[dry-run] {len(result.plan)} step(s) planned. 出力先: {result.output_path}")
        for index, step in enumerate(result.plan, start=1):
            if isinstance(step, WriteFileStep):
                print(f"  {index}. [write] {step.path}")
            elif isinstance(step, CopyFileStep):
                print(f"  {index}. [copy] {step.src} -> {step.dst}")
            elif isinstance(step, CommandStep):
                print(f"  {index}. [command] {step.description}: {step.argv!r}")
    else:
        print(f"生成しました: {result.output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bokurobo", description="ぼくが考えた変形ロボ 動画生成CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="episode.json を検証する")
    validate_parser.add_argument("manifest", help="検証対象の episode.json へのパス")
    validate_parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="image/audio/sfx/font/output の相対パス解決の基準ディレクトリ（省略時はリポジトリルート）",
    )
    validate_parser.set_defaults(func=cmd_validate)

    render_parser = subparsers.add_parser("render", help="episode.json から動画を合成する")
    render_parser.add_argument("manifest", help="renderするepisode.json へのパス")
    render_parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="image/audio/sfx/font/output の相対パス解決の基準ディレクトリ（省略時はリポジトリルート）",
    )
    render_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ffmpegを実行せず、実行予定の各ステップ（ファイル書き込み/ffmpeg argv）を表示する",
    )
    render_parser.set_defaults(func=cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
