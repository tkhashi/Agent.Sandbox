# VanishingPointDetecter

画像内の消失点（vanishing point）を検出するエージェント。VLM（Vision-Language Model）による透視法（パースペクティブ）の意味的な分類と、古典的な画像処理アルゴリズムによる幾何計算を組み合わせて動作する。

- VLM: 画像を見て「一点透視／二点透視／三点透視／該当なし」を判断する意味理解の役割
- 古典CV: 直線検出・クラスタリング・交点推定など、数値的に厳密な幾何計算の役割

この役割分担により、VLM単体では不安定になりがちな座標計算をOpenCVに任せつつ、透視法の種類判定のような曖昧な判断はVLMに委ねている。

## 主要ライブラリ

| ライブラリ | 用途 |
|---|---|
| `opencv-python` (`cv2`) | グレースケール変換、GaussianBlur、Cannyエッジ検出、LineSegmentDetector(LSD)、k-meansクラスタリング、描画 |
| `numpy` | 角度計算（`arctan2`）、距離計算（`hypot`）、RANSAC用の乱数生成 |
| `Pillow` (`PIL`) | 中間画像（エッジ／線分／クラスタ／消失点オーバーレイ）の生成・保存 |
| `PyYAML` | `config.yaml` の読み込み |
| `ollama` | ローカルVLM（Gemma3等）との連携 |
| `openai` | OpenAI API（gpt-4o-mini等）との連携 |

## 処理フロー

エントリポイントは `vp_agent/main.py` の `main()`。設定ファイル（`config.yaml`）を読み込み、VLMクライアントと `CVToolkit` を生成した上で `run_agent()` を呼び出す。`run_agent()` は以下のステップを最大 `max_steps`（デフォルト8）回繰り返すエージェントループとして実装されている。

### ステップ0. 画像読み込み

`run_agent()`（`vp_agent/main.py:151-154`）

- `cv2.imread()` で入力画像を読み込み、画像の高さ・幅を取得する
- 読み込み失敗時は即座にエラーを返す

### ステップ1. 透視法分類（VLM）

`classify_perspective` ツール（`vp_agent/main.py:85-116`、`vlm.classify_and_act` 呼び出しは `main.py:183`）

- VLMが画像を見て、透視法の種類を `one_point` / `two_point` / `three_point` / `none` のいずれかに分類する
- 併せて確信度（`confidence`）、各タイプごとのスコア（`scores`）、判断根拠（`reasoning`）も取得する
- `none` と判定された場合はループを終了する

### ステップ2. 直線検出（古典CV）

`CVToolkit.detect_lines()`（`vp_agent/cv/toolkit.py:31-53`）

1. `cv2.cvtColor(BGR2GRAY)` でグレースケール化
2. `cv2.GaussianBlur((5, 5))` でノイズ除去のためぼかし処理
3. `cv2.Canny(canny_low, canny_high)` でエッジ検出（デフォルト閾値 50/150）
4. `cv2.createLineSegmentDetector(0)`（LSD: Line Segment Detector）でエッジ画像から線分を検出
5. 長さ20px以下の短い線分をノイズとして除去

検出された線分の本数が閾値（デフォルト5本、`validators.is_too_few_lines`）を下回った場合、`main.py:191-196` でCannyの閾値を自動的に緩めて（`-20`／`-50`、下限は10／50）再検出する。

出力先ディレクトリが指定されている場合は、中間画像として `01_edges.jpg`（エッジ画像）と `02_lines.jpg`（検出線分の重畳画像）を保存する。

### ステップ3. 消失点推定（古典CV）

`CVToolkit.find_vanishing_points()`（`vp_agent/cv/toolkit.py:55-73`）

1. 線分が3本未満の場合は空リストを返す
2. 各線分の角度を `Line.angle()`（`np.arctan2`）で算出し、`[-π/2, π/2]` の範囲に正規化する
3. **角度クラスタリング**: `_cluster_by_angle()`（`vp_agent/cv/toolkit.py:127-148`）が `cv2.kmeans`（`KMEANS_PP_CENTERS`）を用いて、透視法の種類に応じたクラスタ数（1〜3）に線分を分類する
4. **RANSACによる交点推定**: 各クラスタごとに `_ransac_intersection()`（`vp_agent/cv/toolkit.py:150-183`）を実行する
   - クラスタ内から2本の線分をランダムに抽出し、`_line_intersection()`（連立方程式 `ax + by = c` を解く）で交点候補を求める
   - 全線分について交点候補までの距離（`_point_to_line_distance()`）を計算し、10px未満の線分をinlierとしてカウント
   - 反復回数は `min(100, n*(n-1)/2)`、乱数シードは `42` に固定し再現性を確保
   - 最もinlierの多い交点を採用し、`support_lines`（inlier数）と `confidence`（inlier数／全体数）を付与した `VanishingPoint` として返す

出力先ディレクトリが指定されている場合は、`03_clusters.jpg`（クラスタごとの色分け画像）と `04_vanishing_points.jpg`（消失点オーバーレイ画像）を保存する。

### ステップ4. 妥当性検証・再試行制御

`validators.is_valid_vp_result()`（`vp_agent/validators.py:4-23`）、呼び出し元は `vp_agent/main.py:205-224`

以下のいずれかに該当する場合、その消失点は無効と判定される。

- クラスタ内の支持直線数（`support_lines`）が `min_cluster_lines`（デフォルト3）未満
- 消失点が画像中心から `max_vp_distance_ratio`（デフォルト50倍）以上離れている

無効と判定された場合は再試行カウンタ（`state["attempts"]`）を増やし、`max_retry_attempts`（デフォルト2）に達していればループを打ち切り失敗を返す。達していなければ検出結果をリセットし、VLMに `retry_with_adjusted_params` ツールを呼ばせてCannyパラメータを調整した上でステップ2からやり直す。

### ステップ5. 結果出力

`vp_agent/main.py:226-247`

`status`・`perspective_type`・`confidence`・`scores`・`reasoning`・`vanishing_points`（各消失点の座標と支持直線数）をまとめた辞書を構築し、出力先ディレクトリが指定されていれば `result.json` として保存、あわせて標準出力にもJSONとして出力する。

## 処理フロー図

```
main()
 └─ run_agent()
     ├─ cv2.imread(画像) → 高さ・幅を取得
     └─ for step in range(max_steps):
         ├─ [1] classify_perspective (VLM)
         │      one_point / two_point / three_point / none を判定
         ├─ [2] detect_lines (CV)
         │      グレースケール化 → GaussianBlur → Canny → LSD検出 → 短線分除去
         │      （検出数不足時はCannyパラメータを自動的に緩めて再検出）
         ├─ [3] find_vanishing_points (CV)
         │      角度算出 → k-meansで角度クラスタリング
         │      → 各クラスタでRANSAC交点推定 → VanishingPoint群
         ├─ [4] is_valid_vp_result で妥当性検証
         │      NG → retry_with_adjusted_params (VLM) でパラメータ調整し [2] へ戻る
         │      OK → ループ終了
         └─ 中間画像・result.json を出力
```

## 主要ファイル対応表

| ファイル:行 | 関数／クラス | 役割 |
|---|---|---|
| `vp_agent/main.py:250` | `main()` | CLIエントリポイント |
| `vp_agent/main.py:143` | `run_agent()` | エージェントループ本体（VLM判断→CV計算→検証を繰り返す） |
| `vp_agent/main.py:30` | `dispatch_tool()` | VLMからのツール呼び出しを `CVToolkit` の実処理にディスパッチ |
| `vp_agent/main.py:85` | `_select_tools()` | 現在の状態に応じてVLMに提示するツールを絞り込む |
| `vp_agent/main.py:98` | `_make_step_prompt()` | 各ステップでVLMに送るプロンプトを生成 |
| `vp_agent/main.py:118` | `_save_intermediate()` | 中間結果（エッジ／線分／クラスタ／消失点画像）を保存 |
| `vp_agent/main.py:17` | `build_vlm_client()` | 設定に応じてVLMクライアント実装を選択・生成 |
| `vp_agent/cv/toolkit.py:31` | `CVToolkit.detect_lines()` | Canny + LSDによる直線検出 |
| `vp_agent/cv/toolkit.py:55` | `CVToolkit.find_vanishing_points()` | 角度クラスタリング＋RANSACによる消失点推定 |
| `vp_agent/cv/toolkit.py:127` | `CVToolkit._cluster_by_angle()` | 線分の角度をk-meansでクラスタリング |
| `vp_agent/cv/toolkit.py:150` | `CVToolkit._ransac_intersection()` | クラスタ内の直線群からRANSACで交点（消失点）を推定 |
| `vp_agent/cv/toolkit.py:185` | `CVToolkit._line_intersection()` | 2直線の交点を連立方程式で算出 |
| `vp_agent/cv/toolkit.py:203` | `CVToolkit._point_to_line_distance()` | 点と直線の距離を算出（inlier判定用） |
| `vp_agent/validators.py:4` | `is_valid_vp_result()` | 消失点結果の妥当性チェック（支持直線数・距離） |
| `vp_agent/validators.py:26` | `is_too_few_lines()` | 検出直線数が少なすぎないかチェック |
| `vp_agent/tools_schema.py` | `TOOLS` | VLMに渡すツール定義（JSON Schema） |
| `vp_agent/vlm/base.py` | `VLMClient` | VLM抽象基底クラス（`classify_and_act`） |
| `vp_agent/vlm/ollama_client.py` | `OllamaVLMClient` | Ollama（Gemma3等）によるVLM実装 |
| `vp_agent/vlm/openai_client.py` | `OpenAIVLMClient` | OpenAI APIによるVLM実装 |

詳細な設計思想・アーキテクチャについては [`vanishing_point_agent_spec.md`](./vanishing_point_agent_spec.md) を参照。
