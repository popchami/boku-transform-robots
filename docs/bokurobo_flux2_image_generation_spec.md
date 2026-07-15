# bokurobo: Flux.2 Klein 9Bによる変形前後2枚画像生成方式

## 1. 目的

bokurobo（お題→変形前後2枚画像→Wan 2.2 FLF2Vで変形動画）のうち、**変形前後2枚の画像生成**をFlux.2 Klein 9Bで行う方式を定義する。

## 2. ベース

`flux2_klein_48GB_workflow_v2ollama.json`（comfyui-mobile-system、48GB tier）をベースに、bokurobo向けへ差分で簡略化した。bokuroboのRunPod運用が48GB VRAMのため48GB tierを採用。

## 3. 変更点（前→後）

| 要素 | 現行（mobile-system用） | bokurobo用 |
|---|---|---|
| Wildcard結合（13種） | あり | なし（背景・カメラ固定のため不要） |
| Face Detailer | あり | なし（人物ではないため不要） |
| Upscale | あり | 省略（最小構成優先、後で追加可） |
| LoRA | あり（複数） | 未使用（今回は省略。指定LoRAができたら追加） |
| 生成枚数 | 1枚 | before/after 2枚を同一ワークフロー内で生成 |
| Seed | randomize | **fixed・変形前後で完全同一値** |
| Ollama翻訳 | 1系統・アダルト向けsystem prompt | 2系統・子供向けロボ変形シーン用system promptに書き換え |

## 4. 背景・カメラ一致の担保方式

3案のうち **A案（Seed固定＋プロンプト文言の一致）** を採用。

| 案 | 方法 | 状態 |
|---|---|---|
| **A（採用）** | KSamplerのseedを変形前後で同一固定値にし、プロンプト側で背景・カメラ記述を共通化 | 今回実装 |
| B | 1枚目生成→2枚目はi2i（低denoise）で変形部分のみ変更 | 未実装。mobile-systemのi2iノード（LoadImage→VAEEncode）を流用可能。A案で背景ズレが出たら次点 |
| C | ControlNet（Canny/Depth）で構図を転写 | 未実装。Flux.2でのControlNet対応は**未確認・要環境確認** |

A案がうまくいかない場合はB→Cの順に拡張する。

## 5. ワークフロー構成

ファイル: `workflows/flux2_klein_bokurobo_beforeafter_v1.json`

### 共有ブロック
- `UNETLoader`：Flux.2 Klein 9B FP8（`flux-2-klein-9b-fp8.safetensors`）
- `CLIPLoader`：Qwen3-8B FP8（`qwen_3_8b_fp8mixed.safetensors`）
- `VAELoader`：flux2-vae
- `OllamaConnectivityV2`：qwen3.5ライン（`jaahas/qwen3.5-uncensored:9b`）
- `CLIPTextEncode`（Negative）：変形前後で共通の1本
- `EmptyLatentImage`：768×1344（**仮値・要確認**、9:16ショート動画想定）

### 変形前ブランチ / 変形後ブランチ（各自）
1. `StringConcatenate`：日本語プロンプト貼り付け用（プレースホルダー入り）
2. `OllamaGenerateV2`：JP→EN翻訳専用（system promptは翻訳のみを厳命、脚色・創作禁止）
3. `CLIPTextEncode`（Positive）：翻訳結果を自動流し込み
4. `FluxGuidance`：guidance=2
5. `KSampler`：**seed=20260715固定**（両ブランチ完全同一）、steps=10、scheduler=simple、sampler=dpmpp_2m、denoise=1、control_after_generate=fixed
6. `VAEDecode`
7. `SaveImage`：`bokurobo_before` / `bokurobo_after`

MODEL/CLIP経路はLoRA未使用のためLoaderから各ノードへ直結（分断リスクなし）。

## 6. Ollama翻訳system prompt（変更点）

既存のアダルト向けsystem promptから、bokurobo向けに以下へ差し替え：

```
/no_think
You are a translator for image generation using FLUX.2 Klein.
This is for a children's short video about a toy-like transforming robot.
Translate the Japanese input to English prose exactly as written.

Output rules:
- Translation only. No additions, no omissions, no embellishment.
- Keep all specified details (background, camera angle, part-to-part correspondence) exactly as given.
- Maximum 100 words.
- Prose format, not tag lists.
```

Ollamaの役割は翻訳のみ。panel lines・背景/カメラ固定・部位対応（画面→胸、スタンド→脚、フレーム→腕 等）の記述は、日本語プロンプト側に既に仕込まれている前提で、それを削らず訳すことのみを指示している。

## 7. 使う人がやること

- [ ] `StringConcatenate`（ユーザー入力・変形前）に、変形前オブジェクトの日本語プロンプトを貼り付け
- [ ] `StringConcatenate`（ユーザー入力・変形後）に、変形後ロボの日本語プロンプトを貼り付け（背景・カメラ記述は変形前と同一文言にする）
- [ ] `EmptyLatentImage`の解像度が動画側（Wan 2.2 FLF2V）の想定サイズと合っているか確認・必要なら変更
- [ ] 生成後、2枚の背景・カメラのズレを目視確認。ズレが大きい場合はB案（i2i）検討をこのチャットに相談

## 8. 未確認・要検証事項

- `EmptyLatentImage`の解像度768×1344は暫定値。bokuroboの動画アスペクト比（縦9:16想定）に合っているか未確認
- A案（seed固定＋文言共通）だけでどこまで背景・カメラが一致するかは未検証。RunPod実機でのテスト結果待ち
- Flux.2 Klein向けControlNet対応ノードの有無は未確認

## 9. 今後の拡張

- B案（i2i差分変更）・C案（ControlNet構図転写）への切り替え
- episode.jsonとの連携（お題→ペアプロンプト自動生成、品質確認後に着手予定）
- LoRA追加（パネルライン等メカ表現用、必要になれば）
