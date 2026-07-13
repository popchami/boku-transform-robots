"""bokurobo CLI エントリポイント。

現時点では `validate` サブコマンドのみ実装する。`render`（動画合成）は
未実装のプレースホルダで、実行すると非0で終了する。

使い方:
    python -m bokurobo.cli validate episodes/<話数>/episode.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .manifest import ManifestError, load_manifest, validate_episode

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
    print("render はまだ実装されていません（現時点では validate のみ対応）", file=sys.stderr)
    return 2


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

    render_parser = subparsers.add_parser("render", help="（未実装）動画を合成する")
    render_parser.set_defaults(func=cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
