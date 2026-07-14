# CODEX_REVIEW_RULES（Codexレビュー運用ルール）

## 基本ルール

- **実装（コード作成）に着手する前に、必ずCodexへagmsgでレビュー依頼を送る**
- レビュー対象は、設計ドキュメント一式（README.md / docs/SPEC.md / docs/WORKFLOW.md / docs/REPO_RESEARCH.md）と、これから書こうとしている実装方針
- Codexからの指摘・懸念点は、実装前にこのリポジトリのIssue的な位置づけ（docs内の該当ファイルへの追記、または本ファイルの「レビュー履歴」セクション）で記録する
- Codexの受信方式は `turn` ではなく `monitor` を使用し、継続的にメッセージを確認する（`/root/.claude/CLAUDE.md` の共通ルールに準拠）

## レビュー依頼のタイミング

1. 初回: リポジトリ構成・ドキュメント一式が揃った時点（設計フェーズの区切り）
2. 実装方針を大きく変更する時（例: TTSエンジンや画像生成手段を確定させる時）
3. 新しい工程（音声合成・動画合成など）の実装に着手する前

## レビュー依頼に含める情報

- 変更・新規作成したファイルの一覧
- 現在の未決定事項（`docs/SPEC.md` の「未決定事項」セクション、`docs/WORKFLOW.md` の「未確定事項」セクション）
- 確認してほしい観点（例: Termux/PC/RunPod横断での実行可否、ライセンス面、設計の矛盾がないか）

## レビュー履歴

| 日付 | 依頼内容 | Codexの回答概要 | 対応 |
|---|---|---|---|
| 2026-07-13 | 初期設計一式（README/SPEC/WORKFLOW/REPO_RESEARCH/CODEX_REVIEW_RULES, 8dc638b）のレビュー | 方針は整合、重大な矛盾なし。TTS・画像生成は今固定せず保留が妥当。最小安全実装として「生成済み素材を受け取りFFmpeg合成するvalidateコア」を提案。TTSは公式調査の結果、Piperは現行版で日本語voice未提供＋v1.3以降GPLv3、旧rhasspy版はarchive済みのため既定候補から除外。edge-ttsはオンライン依存のためoptional provider扱いに限定。字幕タイミングは初版は台本/manifestで手動秒指定（ASRは後日差し替え）。実装はepisode.json（標準JSONのみ、追加依存なし）のvalidator + unittest + `validate`専用CLI scaffoldから着手し、render本体・TTS・画像生成は次段階でレビューしてから着手する方針。 | 下記「決定事項」に反映。src/bokurobo/manifest.py・cli.py、tests/test_manifest.py、episodes/_template/episode.json を実装 |
| 2026-07-13 | manifest validator + CLI実装(1806c55)のコードレビュー依頼 | P1×2件（outputパストラバーサル、asset絶対パス/トラバーサル）、P2×2件（JSON型検証不足、NaN/Infinity/未知transition）を指摘。render着手前の必須修正として承認保留 | b560364で修正、テスト15件追加（計32件）、Codex側で再検証しPASS・承認済み |
| 2026-07-14 | 変形シーンAI動画生成への方針転換（2026-07-14）を踏まえた設計見直し、およびScene.video対応（未コミット差分）の実装レビュー依頼。初回指摘4点: (1)非transformシーンのvideo指定はファイル有無に関係なくerror (2)transformのimage+video同時指定を禁止しerror (3)SPEC(2026-07-14版)に合わせtransformのvideo必須化（静止画フォールバック廃止） (4)videoの許容拡張子(.mp4のみ)を検証 | ユーザーフィードバックを受けた方針転換は下記「決定事項」に反映済み。実装方針は妥当と回答。実装後、独立再検証で39 tests PASS・`git diff --check` OKを確認し承認 | `docs/SPEC.md`・`docs/WORKFLOW.md`の決定事項を更新、`src/bokurobo/manifest.py`・`tests/test_manifest.py`・`tests/test_cli.py`・`episodes/_template/episode.json`を更新（コミットはせずレビュー待ち） |

実施したら、このテーブルに追記する。

## 決定事項（2026-07-13 Codexレビューを反映）

- **TTSエンジン**: 未確定のまま据え置き。Piperは現行版で日本語voice未提供＋GPLv3のため既定候補から除外。edge-ttsはオンラインEdgeサービス依存のためoptional provider扱いに限定し、初版では確定させない。当面は事前生成済み音声ファイルを入力として扱う
- **テロップのタイミング取得方法**: 初版は台本/`episode.json`のシーン単位で手動秒指定とする（15〜20秒の短尺では再現性・Termux負荷面で優位なため）。ASR（音声認識による自動抽出）は将来差し替え候補として残す
- **機械可読データ形式**: `episodes/<話数>/episode.json`（標準JSONのみ、追加ライブラリ不要）。人間向けの`plan.md`と併用する
- **パイプライン分割**: `validate`（マニフェスト検証）→`render`（動画合成、未実装）の2段構成。今回は`validate`のみ実装
- **依存方針**: Python標準ライブラリ中心、外部Pythonパッケージ追加なし。ffmpeg/ffprobeは実行時依存（未導入でも`validate`は動作し、該当チェックは警告に留める）

## 決定事項（2026-07-14 ユーザーフィードバックを反映）

- **変形シーンの表現方針を変更**: 当初「いきなり重い動画生成AIは使わない」方針だったが、ユーザーから「変形は動画で見せたい（高品質にしたい）」というフィードバックがあり方針転換。**「変形」シーンのみ**image-to-video系のAI動画生成を使う。他4シーン（お題提示・変形前・変形後・オチ）は引き続き静止画＋ズーム/切り替え
- Codexが提示していたrender設計論点のうち「(1) transition=cut/zoomの意味」は、変形シーンについては動画クリップの合成に置き換わるため、静止画向けのcut/zoom定義とは別に扱う必要がある。他の論点（caption表示規則、無音/尺差処理、sfx音量、出力固定値、fit/crop、ffmpeg不在時挙動、filter escaping、font要件、テスト戦略）は静止画4シーン分については従来通り検討を進めてよい
- 具体的なimage-to-videoモデル・実行環境・VRAM要件・生成時間・コスト試算は**未検証**。本実装前に調査とCodexレビューが必要（詳細は`docs/SPEC.md`「変形シーンのAI動画生成」参照）
