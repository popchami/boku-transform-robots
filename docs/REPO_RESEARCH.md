# REPO_RESEARCH（既存ショート動画生成リポジトリ調査）

調査日: 2026-07-14。各リポジトリのREADME・GitHub API（license, star数, push日時等）をもとに調査。READMEに明記のない項目は「未確認」と明記する。既存リポジトリを丸ごと採用することはせず、実装パターンの参考のみに用いる。

## 横断比較表

| リポジトリ | ライセンス | Docker必須 | GPU | 外部API課金 | 画像/映像ソース | TTS | 字幕 | Star |
|---|---|---|---|---|---|---|---|---|
| [gyoridavid/short-video-maker](https://github.com/gyoridavid/short-video-maker) | MIT | 推奨 | 任意 | 不要(Pexels無料) | Pexels動画のみ | Kokoro.js(英語のみ) | Whisper.cpp | 1,230 |
| [CosmoJelly/short-form-video-generator](https://github.com/CosmoJelly/short-form-video-generator) | MIT | 不要 | 実質必須 | 不要(ローカル完結) | ユーザー提供動画 | XTTS-v2 | Whisper | 1 |
| [Dark2C/Viral-Faceless-Shorts-Generator](https://github.com/Dark2C/Viral-Faceless-Shorts-Generator) | **不明（LICENSEファイルなし＝全権利留保）** | 必須 | 未確認 | 必要(Gemini) | ユーザー提供動画 | Piper TTS | Aeneas | 85 |
| [SaarD00/AI-Youtube-Shorts-Generator](https://github.com/SaarD00/AI-Youtube-Shorts-Generator)（現AutoShorts AI） | MIT | 不要 | 不要 | 必要(Gemini+Pexels) | Pexels動画+アバター | edge-tts | 未確認 | 172 |
| [ddominguez7/ai-video-generator](https://github.com/ddominguez7/ai-video-generator) | MIT | 必須 | 未確認 | 必要(OpenAI) | なし(固定テンプレート) | OpenAI TTS | Whisper | 1 |

## 各リポジトリの詳細

### 1. gyoridavid/short-video-maker
TikTok/Reels/YouTubeショーツ向けにテキスト入力から動画生成。MCP対応・REST API提供。**AI画像生成機能なし**（背景はPexelsストック動画のみ）。TypeScript/Node.js、Remotion 4.0.286で合成。Docker推奨（tiny/normal/cuda）、RAM 3〜4GB以上必要。TTSは英語専用。作成2025-04、直近push 2025-06-21、中規模で活発（Star 1,230）。

### 2. CosmoJelly/short-form-video-generator
プロンプト→Ollama+Qwen2.5で台本生成→XTTS-v2でナレーション→Whisperで字幕→ユーザー提供のゲームプレイ動画を背景に合成。Python、GPU実質必須。外部API課金は不要だが初回にモデル自動DL。Star 1・コミット4件で実質メンテナンスなしの個人プロジェクト。

### 3. Dark2C/Viral-Faceless-Shorts-Generator
Google Trends→Gemini AIで脚本→Piper TTS→Aeneasで強制アライメント字幕→FFmpeg合成。**LICENSEファイルなし＝許諾が確認できないため流用しない**（GitHub API上もlicense: null。断定的に「不可」と言い切るのではなく、許諾未確認として扱う）。Docker必須（"Fully containerized"）。Piper TTSは軽量でCPU動作可能な点は参考になるが、ライセンス不明のためコード流用は避ける。Star 85、直近push 2026-05-10で比較的活発。

### 4. SaarD00/AI-Youtube-Shorts-Generator（AutoShorts AI）
Gemini 2.0 Flashで台本→edge-ttsでナレーション→Pexelsから2種のストック動画でA/B分割ビジュアル→アバター動画挿入→FFmpeg xfadeでトランジション。MIT。Docker不要・GPU不要、外部API課金あり（Gemini・Pexels）。ローカル重量モデルがなくTermux/PC/RunPodいずれでも動かしやすい構成。Star 172、直近push 2026-06-26と活発。

### 5. ddominguez7/ai-video-generator
台本生成→OpenAI TTS→Whisperで字幕→固定テンプレート背景でFFmpeg合成。**画像生成なし（テンプレート使用）**。MIT。Docker/Docker Compose必須、OpenAI API課金必須。コミット1件のみで実質放置、参考価値は低い。

## 「ぼくが考えた変形ロボ」への示唆

### 採用候補（実装パターンとして参考にする）

- **FFmpegのxfadeトランジション**（SaarD00）: ズーム・切り替え演出を軽量に実現する参考実装として確認する価値あり
- **Whisper/Whisper.cppによる音声→字幕タイミング抽出**（gyoridavid, CosmoJelly, ddominguez7）: 生成済みナレーション音声に単語レベルのタイミングを付ける用途で参考になる。ただしフルモデルはRunPodのセッション再構築コストに注意
- **edge-tts / Piper TTSといった軽量TTSという選択肢**（SaarD00, Dark2C）: コスト重視・Termux動作を優先するなら、OpenAI TTSやXTTS-v2より軽量な選択肢として検討候補。ただし2026-07-14時点の追加調査で、現行OHF-Voice/piper1-gplは公式voice一覧に日本語(ja_JP)なし・v1.3以降GPLv3、旧rhasspy/piperはarchive済みと判明したため、Piperは日本語版の既定TTSにはしない。edge-ttsはオンラインのEdge TTSサービス依存（非公式・ネット必須）のためoptional provider扱いに限定する
- **Remotion(宣言的動画合成)という設計思想**（gyoridavid）: テロップ・ズームをコードで宣言的に組む発想は将来の拡張と相性が良い可能性があるが、Node+Chromiumレンダリングは重くTermuxでは非現実的。PC/RunPod限定候補

### 避ける・採用しない

- **5リポジトリすべてAI画像生成機能を持たない**（Pexelsストック映像／ユーザー提供動画／固定テンプレートのいずれか）。本企画の核である「ComfyUI/Flux発のAI画像」は既存OSSからの流用余地が構造的に薄く、自前で設計する
- **Dark2Cはライセンス不明**のため、コードの直接流用・改変・再配布は行わない。参考閲覧のみ
- **Docker必須構成**（Dark2C, ddominguez7、gyoridavid推奨）はTermux実行との相性が悪く、そのまま輸入しない。Python/シェル＋ffmpeg直呼び出しのようなシンプル構成を優先する
- **重量ローカルモデル前提**（XTTS-v2、Whisper CUDA版等）はRunPod Network Volume 0GB運用（毎セッション再構築）と相性が悪い。軽量クラウドAPIまたは小型ローカルモデルを優先する
- **OpenAI API必須構成**（ddominguez7）は実績が薄く、コスト重視方針とも矛盾するため不採用

### 結論

参考にすべきは画像生成部分ではなく、周辺パイプライン（音声・字幕タイミング・効果音・軽い動画効果の組み合わせ方）。Docker必須構成、重量GPUモデル前提、ライセンス不明コード、有料API必須構成は方針と合わないため避ける。
