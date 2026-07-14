# RENDER_DESIGN（renderサブコマンド 最小設計案・レビュー用）

本ドキュメントは `bokurobo.cli render`（動画合成）の設計と検証結果を記録する。Codexレビュー承認後、`src/bokurobo/render.py`へ実装済み。

前提: `docs/SPEC.md`「変形シーンのAI動画生成」は未検証のため、ここでは「transformシーンの生成済みmp4クリップが既に手元にある」ことを入力として扱う。AI動画生成そのものの実行手段は本設計のスコープ外（別途調査・レビュー）。

## 0. 全体パイプライン

```
load_manifest
  → validate_episode（renderでも必ず再実行。errors=0でなければ即中断）
  → tempfile.mkdtemp() で一時ディレクトリを確保（with文のcontext managerは使わない。3.6参照）
  → シーンごとに「video+captions+audio」を1本のmp4クリップにまとめる（正規化済み・同一エンコード設定）
  → concat demuxer（-c copy）で全クリップを結合 = 最終mp4
  → output_path（validate_episodeが検証したoutput/配下）へ移動
  → 成功時のみtempdirをshutil.rmtreeで明示的に削除。失敗時は削除せず保持し、RenderErrorに絶対パスを含める（3.6参照）
```

シーン単位で音声まで焼き込んだmp4を作ってから`-c copy`で結合する方式を採用する。理由:
- 各シーンのエンコード設定（解像度/fps/pixfmt/音声codec/サンプルレート）を揃えておけば、結合段はコピーのみで済み高速・劣化なし
- シーン単位でエラーの切り分け・再実行がしやすい（Termuxのような非力な環境でも1シーン単位ならデバッグ可能）
- filter_complexで全体を1コマンドにまとめる方式（後述）よりも、失敗時に中間mp4が保持され（3.6参照）失敗箇所の特定が容易

## 1. 関数分割と入出力

```
render_episode(episode: Episode, base_dir: Path, *, dry_run: bool = False) -> RenderResult
    - validate_episode を再実行し errors があれば RenderError を送出（ffmpeg変換コマンドは一切呼ばない）。
      ただしこの再検証自体はvalidate_episode既存実装の一部としてnarration_audioにffprobeを呼びうる
      （dry_run True/False どちらでも実行される。読み取り専用probeであり書き込み・ffmpeg呼び出しではないため）
    - 出力先パスは引数で受け取らない。episode.output から base_dir 基準で解決した「検証済みの1つのパス」だけを使う
      （呼び出し側が任意のoutput_pathを渡してbase_dir外へ書き出せる余地を作らないため）
    - preflight（validate_episode再実行の直後、build_render_plan呼び出し前に必ず行う。dry_run True/False共通。
      いずれもファイルの読み取りのみでffmpeg呼び出し・書き込みは行わないため、dry_runでもスキップしない）:
        1. font必須チェック（3.5参照。captionありシーンが1つでもあり episode.font 未指定なら即RenderError）
        2. transformシーンを含む場合、`shutil.which("ffprobe")`の存在確認（3.2参照。Noneなら即RenderError）
        3. media_info = `_probe_media_info(episode, base_dir)` を呼び、transformシーンの実尺を集めた
           `RenderMediaInfo`（`dict[str, float]`、scene_idキー→probeした実尺秒。詳細は3.2参照）を構築する
    - dry_run=True: 実ディレクトリは作成せず、プレースホルダのtmp_dir（例: Path("<tmp>")、実体を持たないシンボリックなパス）と
      上記media_infoをbuild_render_planに渡し、返ってきたRenderPlanをそのまま返す。ffmpeg呼び出し・ファイル書き込みは一切行わない
    - dry_run=False: 実行直前に`shutil.which("ffmpeg")`の存在確認を行う（Noneなら即RenderError。全シーンでffmpegエンコードが
      必須なため常時必須）。確認後`tempfile.mkdtemp()`で実tmp_dirを作成し（with文は使わない。理由は3.6参照）、
      build_render_plan(episode, base_dir, tmp_dir, media_info) を呼んでRenderPlanを得たうえで、
      RenderPlanの各ステップを順に実行する（WriteFileStepはファイル書き込み、CommandStepはsubprocess.run(shell=False)）。
      あるステップが失敗したら即RenderErrorを送出して中断する。この場合tempdirは削除しない
    - 成功時のみ最終生成物を検証済み出力先パスへmoveし、その後shutil.rmtreeでtempdirを明示的に削除する。失敗時はtempdirを削除せず、
      RenderErrorにtempdirの絶対パスを含める（3.6参照。中間ファイルを調査できるようにするため）

build_render_plan(episode: Episode, base_dir: Path, tmp_dir: Path, media_info: RenderMediaInfo) -> RenderPlan
    - `episode.font`が指定されている場合、元のfont_path（任意の場所・エスケープ困難な文字を含みうる）を
      tmp_dir内の安全な固定名（`font<元の拡張子>`）へコピーする`CopyFileStep`を先頭に積む（2章参照。
      fontfile=に渡すのは常にこの安全なコピー先のみとし、元のfont_pathをフィルタ文字列に直接埋め込まない）
    - シーンごとに build_scene_clip_command を呼び、ステップ列（captionのWriteFileStep群 + CommandStep）とクリップパスを集める
    - 最後に build_concat_command を呼び、最終結合ステップを追加する
    - **純粋関数（ディスクI/O・subprocess呼び出しを一切行わない）**。tmp_dirは実在しなくてもよい単なるPath値として使い、
      media_infoは呼び出し側（render_episode）のpreflightで既に計算済みの値を受け取るだけで、この関数自身はffprobeを呼ばない。
      生成すべきファイルの「内容」はRenderStep（下記）にデータとして保持するだけで、実際の書き込み・コピーはrender_episode側の実行フェーズが行う
    - RenderPlan = list[RenderStep]

RenderStep は以下のいずれか（実装はUnion/dataclassの継承等で表現）:
    - WriteFileStep(path: Path, content: str)        … 例: concat demuxer用listファイル、caption毎のtextfile
    - CommandStep(description: str, argv: list[str], produces: Path)  … 実際のffmpeg呼び出し
    - CopyFileStep(src: Path, dst: Path)              … fontを安全な固定名へコピー（実行時は`shutil.copy2`）

build_scene_clip_command(scene: Scene, base_dir: Path, tmp_dir: Path, media_info: RenderMediaInfo, safe_font_path: Path | None) -> list[RenderStep]
    - シーン1本分の「video(1080x1920, 一定fps) + captions焼き込み + audio(narration+sfx合成、duration_sec長に整形)」を
      1回のffmpeg呼び出しで生成する
    - image シーンと video(transform) シーンで正規化フィルタ部分のみ分岐（後述2）。transformシーンは
      `media_info[scene.id]`の実尺を使ってloop/trimを判定する（3.2参照）
    - captionが無い場合はdrawtextを付けない。narration/sfxが両方無い場合は無音トラックを生成（-f lavfi anullsrc）
    - 全シーン共通で明示的なエンコード設定を固定する（3.3「concat方式」の前提条件を参照）
    - 戻り値は「captionの数だけ生成されるWriteFileStep（3.5参照） + 最後にCommandStep」のリスト。
      `build_render_plan`はこれをflattenしてRenderPlanに積む（最後の要素が必ずCommandStepである前提でclip_pathを取り出す）

build_concat_command(clip_paths: list[Path], tmp_dir: Path, final_path: Path) -> list[RenderStep]
    - concat demuxer用listファイルの内容（各行 `file '<絶対パス>'`、パス中の`'`は`'\''`でエスケープ）を組み立て、
      WriteFileStep(list_path, content) として返す（この時点ではまだファイルに書かない）
    - 続けて CommandStep("concat", ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(final_path)], final_path) を返す
    - 戻り値は [WriteFileStep, CommandStep] の2ステップ
```

`Scene`/`Episode`は`src/bokurobo/manifest.py`の既存dataclassをそのまま使う。renderは新規モジュール（例: `src/bokurobo/render.py`）に切り出し、`manifest.py`の`_resolve_within`等のパス安全性ヘルパーは重複実装せず再利用する想定（`manifest`から関数をimportするか、共通ヘルパーとして切り出すかは実装時に決める軽微な論点）。

## 2. FFmpeg引数列生成（shell=False方針）

`manifest.py`の`_probe_duration_sec`と同じ方針を踏襲する: **常に`list[str]`でargvを組み立て、`shell=True`は使わない**。パスやテキストにシェル的に危険な文字（`;`, `$()`, スペース等）が含まれていても、`subprocess.run(argv, shell=False)`であればシェル展開自体が起きないため、シェルインジェクションの余地がない。

ただし、シェルエスケープとffmpegの**フィルタグラフ内エスケープ**は別問題であることに注意する。`-vf`/`-filter_complex`に渡す文字列（drawtextのtext引数など）は、ffmpegのフィルタパーサ自身が`:`, `'`, `\`, `,`, `[`, `]`, `;` などを特別扱いするため、argvの要素としては安全でも、フィルタ文字列の中身としてはエスケープが必要（既存の`FFMPEG_TEXT_SPECIAL_CHARS`/`_check_ffmpeg_text`が検知している警告に対応する実処理）。これは3.5「字幕（captions）のエスケープ」で扱う。

## 3. 各要素の正規化・処理方針

### 3.1 静止画/動画の1080x1920正規化

- 共通ターゲット（全シーン確定値。concat -c copyの前提条件、詳細は3.3参照）:
    - 映像: 解像度`1080x1920`, ピクセルフォーマット`yuv420p`, フレームレート`fps=30`, コーデック`libx264`, `-profile:v high`, `-level:v 4.0`, `-video_track_timescale 30000`で全クリップのtime_baseを揃える
    - 音声: コーデック`aac`, サンプルレート`48000`, チャンネルレイアウト`stereo`（モノラル素材も`-ac 2`でstereoに揃える）
    - 上記いずれか1項目でもシーン間でズレるとconcat demuxerの`-c copy`が失敗しうるため、build_scene_clip_commandは全シーン共通のコーデック/フォーマット指定を使う。分岐するのは正規化フィルタ部分（下記）のみ
- 静止画シーン: `zoompan`フィルタでKen Burns風のゆっくりしたズーム/パンを掛けつつ、`scale`+`crop`（または`pad`）で1080x1920に収める。`transition="zoom"`ならズーム量を大きめ、`"cut"`なら控えめ or ズーム無しにする（値のマッピングは実装時に具体的な倍率を決める）
- transformシーン（生成済みmp4）: `scale`+`crop`/`pad`で1080x1920に正規化。フレームレートも`fps=30`フィルタで統一

### 3.2 各sceneの尺処理

- 静止画シーンは`zoompan`の`d`（フレーム数）を`duration_sec * fps`で計算して生成するため、常にちょうど`duration_sec`になる（尺不一致は原理的に起きない）
- transformシーン（生成済みクリップ）は元動画の実尺が`duration_sec`と一致しない可能性がある。loop/trimどちらを適用するかの判定には**実尺の値そのものが必須**であり、5.2の未決定事項の推奨デフォルトを適用するにも実尺probeの工程が要る
- **ffprobeはrenderでは必須依存とする**（`validate_episode`とは扱いを変える。validateは"ffprobe未導入なら警告のみに留め動作継続"だが、renderはtransformシーンのloop/trim判定に実尺が要るため代替できない）
    - `shutil.which("ffprobe")`がNoneの場合: transformシーンを含むepisodeのrenderは即`RenderError`（ffmpeg呼び出し前に中断）
    - ffprobeは実行できたが尺を解析できなかった場合（`manifest._probe_duration_sec`が`None`を返すケース）も同様に`RenderError`とする（loop/trimを安全側に倒す判断ができないため、無音/フォールバック的処理はしない）
    - `manifest._probe_duration_sec`をrenderからも再利用する想定だが、上記の通りrender用途では戻り値`None`を許容しない呼び出し側ラッパー（例: `_require_duration_sec`）を新設する軽微な実装差分がある
- probeした実尺は`RenderMediaInfo`（1章参照。`dict[str, float]`、scene_idをキーに実尺秒を保持）として`render_episode`のpreflightが構築し、`build_render_plan`/`build_scene_clip_command`へ引数で渡す。probe（ffprobe呼び出し）自体はrender_episode側のpreflightでのみ行い、`build_render_plan`はmedia_infoを受け取って使うだけの純粋関数のままとする

### 3.3 concat方式

- **concat demuxer**（`-f concat -safe 0 -i list.txt -c copy`）を採用し、**filter_complexによる1本のフィルタグラフでの全編結合は採用しない**
- 理由: 各シーンを個別のffmpeg呼び出しで正規化・確定済みのmp4にしておけば、結合は再エンコード不要（`-c copy`）で高速・低負荷。Termux（非力な環境）での実行を優先する本プロジェクトの方針（`docs/WORKFLOW.md`）に合致する
- トレードオフ: concat demuxerは単純な結合（カット）のみで、`xfade`のようなシーン間クロスフェードは扱えない（クロスフェードには跨ぎフィルタが必要でconcat -c copyの前提が崩れる）。これは5.4「xfade採否」で扱う

### 3.4 音声/SFXのtrim/pad/amix

- シーンの音声トラックは常に`duration_sec`ちょうどの長さに整形してから動画とmuxする（結合後の全体尺とズレないようにするため）
- `narration_audio`がある場合: `atrim`で`duration_sec`より長ければ切り詰め、`apad`で短ければ無音パディング（`validate_episode`が既にffprobeで実尺とduration_secの差を警告している範囲を、render側で機械的に吸収する）
- `sfx`がある場合: `narration_audio`と`amix`で合成する。複数sfxがある場合も同様に全トラックをamix。`amix`は`normalize=0`を明示する（デフォルトの`normalize=1`は入力数に応じて自動減衰するため、5.3決定事項の「各sfxは0dB＝無加工」と矛盾する）。normalize=0はクリッピング（音割れ）が起こりうるトレードオフであり、生成物は目視・聴取での確認を前提とする
- どちらも無い場合: `-f lavfi -i anullsrc=...`で`duration_sec`分の無音トラックを生成し、動画とmuxする（音声トラックが常に存在する状態を保証し、concat時の音声有無の不整合を避ける）
- sfxの開始位置・音量は5.3の決定事項

### 3.5 字幕（captions）とfontfileの安全な受け渡し

- **実FFmpegでの検証結果（2026-07-14スモークテストで発覚）**: 当初案（`text='<escaped>'`にcaption文字列を、`fontfile='<escaped>'`に元のfont_pathを直接埋め込む）は、コロン・シングルクォートを含む現実的な入力で`exit 234`・`No option name near`・`Error parsing filterchain`となり、`_escape_drawtext_text`によるバックスラッシュエスケープだけでは実FFmpegのフィルタパーサの多段エスケープ要件を満たせないことが判明した。バックスラッシュを増やす場当たり対応はせず、**任意の文字列/パスをフィルタ文字列に直接埋め込まない方式**へ変更する
- **caption**: 各captionをそのまま（改行を追加しない）tmp_dir内の安全な固定名ファイル（`{scene.id}_caption_{index}.txt`、UTF-8）へ`WriteFileStep`で書き出し、drawtextは`text=`ではなく`textfile=<そのパス>`で読ませる。ファイル内容はフィルタパーサを経由しないため、caption文字列自体へのエスケープは一切不要になる
- **font**: `fontfile=`に渡すのは元のfont_path（任意の場所・エスケープ困難な文字を含みうる）ではなく、`build_render_plan`が`CopyFileStep`でtmp_dir内の安全な固定名（`font<元の拡張子>`）へコピーした先のパスのみとする（1章参照）
- フィルタ文字列に載るのは結局「tmp_dir配下の、こちらが命名を制御するパス」（`{scene.id}_caption_{index}.txt` / `font<拡張子>`）だけになる。scene.idはREQUIRED_SCENE_IDS由来の固定英数字、拡張子・連番も安全な文字のみで構成されるため、実質的にエスケープが必要な文字は含まれない。ただしtmp_dir自体（OSのテンポラリディレクトリ配下、`tempfile.mkdtemp()`が生成）が万一特殊文字を含む場合に備え、`_escape_drawtext_text`によるフィルタエスケープは念のため引き続き適用する（多重防御）
- 複数captionがあるシーンでの表示配分は5.1の決定事項
- **font必須化とpreflight**: captionを持つシーンが1つでもあり、かつ`episode.font`が未指定の場合、render_episodeはffmpegを一切呼ばずに`RenderError`を送出する（drawtextに`fontfile=`を渡せないため、OS標準フォントへの暗黙フォールバックはしない）。この判定はvalidate_episode再実行後・build_render_plan呼び出し前のpreflightとして行う
- fontファイル自体の破損・非対応フォーマットはffmpeg実行時にしか検出できない。この場合はCommandStepの失敗として扱い、4章記載のRenderError（stderr同梱、3.6参照）で表面化させる。事前の静的preflightはパスの存在確認（validate_episodeが既に実施）までとし、フォント内容自体の妥当性チェックは行わない
- **ffmpeg本体・drawtextフィルタの利用可否（v1方針）**: 事前チェックは1章のpreflightで行う`shutil.which("ffmpeg")`の存在確認のみとする。`drawtext`フィルタ自体が特定のffmpegビルドで無効（fontconfig/freetype未サポート等）なケースを静的に事前検出する仕組みは設けない。`ffmpeg -filters`の出力を事前取得して解析する案は、ffmpegバージョン間での出力表記揺れ・パース信頼性の懸念があるため本ドキュメントでは不採用とする。drawtext欠如は他のffmpeg実行エラーと同様にCommandStepの失敗として顕在化し、末尾4000文字のstderr（3.6参照）で原因を確認する運用とする

### 3.6 一時ファイルと失敗時cleanup

- `render_episode`は`tempfile.TemporaryDirectory()`の`with`文を使わない。**成功時と失敗時でtempdirの扱いを変える必要がある**（失敗時は中間ファイルを調査用に残す）ため、成功/失敗を問わず削除する`with`文の自動削除とは前提が合わない。代わりに`tempfile.mkdtemp()`で明示的にtmp_dirを作成する
- シーン単位クリップ・concat用listファイル・concat結果・caption textfile・コピーしたfontを含め全て同じtmp_dir配下に置く
- `build_render_plan`〜`_execute_plan`〜出力先への`shutil.move`までを1つの`try`で囲む。`RenderError`はもちろん、`subprocess.run`や`Path.write_text`/`shutil.copy2`が送出しうる`OSError`・`subprocess.SubprocessError`、plan構築時の`KeyError`等の想定外例外も`RenderError`へ変換し、tmp_dirの絶対パスをメッセージに含める。**この場合tmp_dirは削除しない**（調査可能な状態のまま残す）
- 全ステップが成功した場合のみ、最終生成物をtmp_dirの外（`output/`配下、`_validate_output_path`で検証済みのパス）へ`shutil.move`する。その後の`shutil.rmtree(tmp_dir)`のみは別扱いとし、失敗しても生成自体は成功しているため`RenderError`にはせず、RenderResult.warningsへの追記に留める
- CommandStepの失敗を`RenderError`に変換する際は、`subprocess.run(..., capture_output=True, text=True)`で取得した`stderr`をメッセージに含める。ただし際限なく長くなり得るため**末尾4000文字までに切り詰めて**含める（ffmpegのエラー本体は末尾に出ることが多いため、先頭ではなく末尾を残す）
- `CopyFileStep`の実行は`shutil.copy2(src, dst)`（メタデータもコピーする標準的な方法。dstはtmp_dir内の安全な固定名）

### 3.7 render直前の再検証（入力パス安全性）

- `render_episode`は呼び出し側が事前に`validate`していたとしても**信用せず**、内部で必ず`validate_episode(episode, base_dir)`を再実行し、`level=="error"`のIssueが1件でもあれば、ffmpegは一切呼ばずに中断する
- パス解決（`_resolve_within`によるbase_dir外/絶対パス拒否）は`manifest.py`の既存実装をrenderからも再利用し、render側で独自にパス解決ロジックを再実装しない（実装乖離によるセキュリティリグレッションを避ける）
- 警告（warning）は中断せず、render結果に含めてCLI側で表示する

## 4. dry-runとmockテストの境界

- `build_render_plan`は**純粋関数**として設計する（subprocessを呼ばず、argv列と出力先パスのリストを返すだけ）。これにより`dry_run=True`時はもちろん、通常実行時も「まずプランを作る→それを実行する」の2段構成になり、テストの主戦場を「プラン生成」に寄せられる。ただし`render_episode`全体で見ると、`build_render_plan`呼び出し前のpreflight（validate_episodeのnarration_audio probe、font必須チェック、media_info構築のffprobe呼び出し。1章参照）は`dry_run`に関わらず実行される点に注意する。「dry_runでsubprocessを一切呼ばない」のではなく、「dry_runでffmpeg変換コマンドを呼ばない」というのが正確な境界線である
- **単体テスト（大半）**: 実ffmpegに依存せず、`build_render_plan`が返すargv列の中身（フィルタ文字列、パス、フラグの有無）を文字列/リストとして直接assertする。captionエスケープや尺処理の分岐（3.1〜3.6）はここで網羅する
- **実行系テスト（少数）**: `subprocess.run`を`unittest.mock`でモックし、`render_episode`が「各ステップを順に呼ぶ」「非ゼロ終了で即中断し、tmp_dirを削除せずRenderErrorにパスとstderrを含める（3.6参照）」「成功時に最終ファイルをoutput_pathへ移動し、その後tmp_dirを削除する」という制御フローを検証する。実ffmpegバイナリは不要
- **スモークテスト（任意・最小限）**: `shutil.which("ffmpeg")`が見つかる環境でのみ`@unittest.skipUnless`で実行し、実際に小さなmp4が1本生成されることを確認する。`validate_episode`の`ffprobe`任意方針と揃え、ffmpeg未導入環境（Termuxの一部構成等）でもテストスイート全体が失敗しないようにする

## 5. 決定事項（2026-07-14 ユーザー確認済み）

Codexから明示提起された4点＋関連事項について、推奨デフォルト案をユーザーに確認し、いずれも推奨案どおりで承認された。**以下は決定事項であり、実装済み（`src/bokurobo/render.py`）。**

### 5.1 captions複数要素のscene内表示配分

- 論点: 1シーンに複数の`captions`文字列がある場合、`duration_sec`内でどう出し分けるか
- 決定: **`duration_sec`を`len(captions)`で均等分割し、順番に`drawtext`の`enable='between(t,start,end)'`で切り替え表示する**
- 理由: 現状のSPEC決定（テロップタイミングはシーン単位手動秒指定、ASRは将来差し替え候補）と矛盾せず、追加のスキーマ変更（caption毎の秒指定等）なしに実装できる。テンプレートも現状1シーン1captionが基本形

### 5.2 短いtransform動画をloopするかエラーにするか

- 論点: transformシーンの生成済みmp4の実尺が`duration_sec`より短い場合の扱い
- 決定: **`-stream_loop -1`でループさせ`duration_sec`ちょうどに`-t`で切り詰める。ただしループ境界で映像が不連続になるリスクがあるため、renderのwarningとして出力に明記し、ユーザーが目視確認できるようにする**（エラーにはしない）
- 理由: AI動画生成の尺は現状未検証（`docs/SPEC.md`）で、生成結果が想定尺ぴったりにならない可能性が高い。生成のたびにエラー停止させるとパイプラインが実用にならないため、まずは自動補完を優先し、品質面の最終判断は目視レビューに委ねる。実尺が`duration_sec`より長い場合は単純に`-t`でtrimする（loopと違い不連続リスクがないため未決定事項からは除外）

### 5.3 SFX開始位置/音量

- 論点: 現状の`Scene.sfx: list[str]`にはファイルパスのみでタイミング・音量の指定手段がない
- 決定: **各sfxはシーン開始（`t=0`）から再生、音量は0dB（無加工）としてnarrationとamixする**（実装は`amix`に`normalize=0`を明示することで各入力を無加工のまま合成する。3.4参照。クリッピングのリスクは目視・聴取確認で受け止める）
- 理由: 追加のスキーマ変更（例: `sfx`をオブジェクトのリストにして`offset_sec`/`volume_db`を持たせる）なしに実装できる最小案。ただし将来的に「決めポーズの一瞬だけSFXを鳴らす」等の演出をしたい場合は`episode.json`のスキーマ拡張（後方互換を保つなら`sfx`を文字列/オブジェクト両対応にする等）が必要になる。これはmanifestのスキーマ変更を伴うため、本ドキュメントの範囲外の別途相談事項とする

### 5.4 xfade採否

- 論点: シーン間の切り替えを`concat demuxer`の単純カットにするか、`xfade`でクロスフェードにするか
- 決定: **v1では不採用。全て`concat demuxer -c copy`によるカットとする**
- 理由: `xfade`はクリップ跨ぎの`filter_complex`が必要になり、3.3で述べた「シーン単位で確定済みmp4を作り`-c copy`で結合する」という高速・低負荷な設計の前提が崩れる（全体再エンコードが必要になり、Termuxでの負荷が増す）。`docs/SPEC.md`でも`transition`は"cut"/"zoom"の2値のみが定義されており、"zoom"はシーン内の`zoompan`演出として既に表現できるため、クロスフェード自体を導入する必然性が現時点では薄い。将来ユーザーから明示的な要望があれば、シーン単位concat方式から「隣接シーンのみfilter_complexで再エンコードする」ハイブリッド方式へ拡張する余地を残す

## 6. 単一filter_complex方式との比較（参考）

| 観点 | 本設計（シーン単位クリップ＋concat demuxer） | 単一filter_complex（全編1コマンド） |
|---|---|---|
| Termux等非力環境での負荷 | シーン単位で完結する分、途中経過を確認しながら段階的に実行できる。全体を1コマンドにする場合よりメモリ/CPU負荷のピークを抑えやすい | 1コマンドで全シーンのフィルタグラフを保持するため、シーン数・尺が増えるほどグラフが複雑化し負荷・メモリ使用量が読みにくい |
| デバッグのしやすさ | 失敗したシーンの中間mp4が残るため、どのシーンのどのフィルタで失敗したか特定しやすい | 失敗時にどのフィルタ由来のエラーか切り分けにくい（フィルタグラフ全体のエラーとして出る） |
| 結合コスト | `-c copy`でほぼ無劣化・高速 | 全体を一度に再エンコードするため確実だが、シーン単位再実行ができず、1シーンだけ直したい時も全体を再実行する必要がある |
| クロスフェード対応 | 不可（5.4の通り将来ハイブリッド拡張の余地はある） | 可能（`xfade`をグラフに組み込みやすい） |
| 実装・テストの見通し | 関数を機能単位（正規化/尺処理/音声/字幕/結合）に分割しやすく、4章のdry-run/mock境界とも相性が良い | フィルタグラフ全体を一度に構築する関数になりやすく、単体テストの粒度が粗くなりやすい |

以上の比較から、本ドキュメントでは**シーン単位クリップ＋concat demuxer方式**を採用する。

## 7. このドキュメントのスコープ外

- transformシーンのAI動画生成そのもの（モデル・実行環境・VRAM・コスト試算、`docs/SPEC.md`参照、未検証）
- TTSエンジンの選定（`docs/CODEX_REVIEW_RULES.md`決定事項参照、未確定）
- `episode.json`のスキーマ変更を伴う機能（sfxのオフセット/音量、caption毎の秒指定等）

## 8. 実FFmpegスモークテスト検証結果（2026-07-14、Codex実施）

ユーザー許可のもとffmpeg/ffprobeを導入し、モックではなく実バイナリでrenderを検証した。

- **通常ケース（captions/font無し5シーン）**: render成功。ffprobeで最終出力を確認し、`duration=20.021333`秒、映像は`H.264 1080x1920 30fps`、音声は`AAC 48000Hz stereo`（3.1の確定エンコード設定どおり）。durationが総`duration_sec`（20.0秒ちょうど）よりわずかに超過する挙動が観測されているが、5シーンのconcat -c copyにおけるGOP/キーフレーム境界起因の誤差とみられ、本ドキュメントの範囲では追加調査事項として扱う（致命的な不整合ではない）
- **特殊文字ケース（コロン・シングルクォートを含むfont path、コロン・カンマ・シングルクォートを含むcaption）**: `exit 234`、`No option name near`、`Error parsing filterchain`で失敗。当初の`text=`/`fontfile=`直接埋め込み＋`_escape_drawtext_text`方式では実FFmpegの多段エスケープ要件を満たせないことが判明し、3.5記載の「caption textfile化 + fontコピー」方式へ設計変更した（本節はその根拠となった実測記録）
- **特殊文字ケース再検証（修正版・PASS）**: 3.5記載のtextfile化 + fontコピー方式へ変更後、コロン・シングルクォートを含むfont pathおよびコロン・カンマ・シングルクォートを含むcaptionを含む5シーンフルrenderをCodex側で実施し成功を確認（出力: `/tmp/bokurobo_smoke/output/special.mp4`）。あわせて、`validate_episode`が出していた「captionのffmpegエスケープ要求」warning（`_check_ffmpeg_text`）は、render側がcaption本文をtextfile=経由で渡す（フィルタパーサを経由しない）ため実態と合わなくなったとして削除した（`FFMPEG_TEXT_SPECIAL_CHARS`定数自体はrender.pyの`_escape_drawtext_text`が引き続き使用するため維持）
