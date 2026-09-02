# 消失点検出Agent 仕様書

## 1. 概要

画像を入力とし、透視法の種類（一点/二点/三点/該当なし）をVLMが判定した上で、
その判定に応じた古典CVアルゴリズム（直線検出＋交点クラスタリング）で消失点座標を
算出するAgent。VLM部分はローカル(Ollama/Gemma3:4b)とクラウド(OpenAI API)を
実行時に切り替え可能な形で抽象化する。

- **判断（意味的分類）**: VLM が担当
- **計算（幾何学的精密計算）**: 古典CV（OpenCV）が担当
- **検証・再試行判断**: 数値的な妥当性チェックにより自動判定（Judge不要）

---

## 2. アーキテクチャ

```
┌─────────────┐     画像      ┌──────────────────┐
│   main.py   │ ────────────> │  VLMClient (ABC)  │
│ (Agentループ) │ <──────────── │  ├ OllamaVLM     │
└──────┬──────┘   判定結果+ツール呼び出し │  └ OpenAIVLM      │
       │                       └──────────────────┘
       │
       v
┌─────────────────┐
│  CVToolkit       │
│  ├ detect_lines  │  (Canny + LSD/Hough)
│  ├ cluster_vps   │  (RANSAC / J-Linkage)
│  └ validate_vp   │  (妥当性チェック)
└─────────────────┘
```

### 設計方針
- `VLMClient` は抽象基底クラス（ABC）とし、`OllamaVLMClient` / `OpenAIVLMClient` の
  2実装を用意する。呼び出し側（Agentループ）はどちらの実装かを意識しない。
- 切り替えは設定ファイル（`config.yaml` or 環境変数）の1箇所のみで行う。
- ツール呼び出し（function calling）のインターフェースは共通スキーマ（JSON Schema）
  で定義し、両プロバイダで同一のツール定義を使い回す。

---

## 3. VLM抽象化レイヤー

### 3.1 共通インターフェース

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]

@dataclass
class VLMResponse:
    text: str | None          # テキスト応答（あれば）
    tool_calls: list[ToolCall]  # 呼び出されたツール（あれば）
    raw: Any                  # デバッグ用の生レスポンス

class VLMClient(ABC):
    @abstractmethod
    def classify_and_act(
        self,
        image_path: str,
        messages: list[dict],
        tools: list[dict],  # JSON Schema形式のツール定義（共通）
    ) -> VLMResponse:
        """画像+会話履歴+ツール定義を渡し、モデルの判断結果を返す"""
        ...
```

### 3.2 Ollama実装（Gemma3:4b）

```python
class OllamaVLMClient(VLMClient):
    def __init__(self, model: str = "gemma3:4b", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host

    def classify_and_act(self, image_path, messages, tools) -> VLMResponse:
        # ollama.chat(model=..., messages=[...images付き...], tools=tools)
        # Gemma3のtool-use精度が不安定な場合に備え、
        # レスポンスのJSONパースに失敗したら1回だけ
        # 「JSON形式で再出力して」というリトライを内部で行う
        ...
```

**Gemma3:4b特有の注意点（実装に織り込む）**
- tool callingのJSON整形が崩れることがあるため、パース失敗時の**自動リトライ（最大1回）**を
  `OllamaVLMClient` 内に実装しておく（呼び出し側からは見えない詳細として隠蔽）。
- ツール数は最大3〜4個に制限。
- 画像はbase64 or PILで直接 `ollama.chat` の `images` 引数に渡す。

### 3.3 OpenAI実装（フォールバック用）

```python
class OpenAIVLMClient(VLMClient):
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def classify_and_act(self, image_path, messages, tools) -> VLMResponse:
        # openai.chat.completions.create(model=..., messages=[...], tools=tools)
        # tool_calls をそのままVLMResponseに詰め替えるだけ（リトライ処理は不要）
        ...
```

### 3.4 切り替え方法

```yaml
# config.yaml
vlm:
  provider: "ollama"   # "ollama" | "openai"
  ollama:
    model: "gemma3:4b"
    host: "http://localhost:11434"
  openai:
    model: "gpt-4o-mini"
    api_key_env: "OPENAI_API_KEY"
```

```python
def build_vlm_client(config: dict) -> VLMClient:
    provider = config["vlm"]["provider"]
    if provider == "ollama":
        return OllamaVLMClient(**config["vlm"]["ollama"])
    elif provider == "openai":
        return OpenAIVLMClient(**config["vlm"]["openai"])
    raise ValueError(f"unknown provider: {provider}")
```

呼び出し側（Agentループ）は `VLMClient` 型としてのみ扱うため、
`config.yaml` の1行を書き換えるだけでGemma3↔OpenAIを切り替え可能。

---

## 4. ツール定義（共通スキーマ）

両プロバイダで共有するツール（JSON Schema）。

```python
TOOLS = [
    {
        "name": "classify_perspective",
        "description": "画像を見て透視法の種類を判定する",
        "parameters": {
            "type": "object",
            "properties": {
                "perspective_type": {
                    "type": "string",
                    "enum": ["one_point", "two_point", "three_point", "none"]
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "low"]
                },
                "reasoning": {"type": "string"}
            },
            "required": ["perspective_type", "confidence"]
        }
    },
    {
        "name": "detect_lines",
        "description": "Canny+LSDで画像から直線群を検出する（CV側で実行）",
        "parameters": {
            "type": "object",
            "properties": {
                "canny_low": {"type": "integer", "default": 50},
                "canny_high": {"type": "integer", "default": 150}
            }
        }
    },
    {
        "name": "find_vanishing_points",
        "description": "検出済み直線群から指定数の消失点をRANSACでクラスタリング推定する",
        "parameters": {
            "type": "object",
            "properties": {
                "num_vanishing_points": {"type": "integer", "enum": [1, 2, 3]}
            },
            "required": ["num_vanishing_points"]
        }
    },
    {
        "name": "retry_with_adjusted_params",
        "description": "検出結果が不十分な場合、エッジ検出のパラメータを変更して再試行する",
        "parameters": {
            "type": "object",
            "properties": {
                "canny_low": {"type": "integer"},
                "canny_high": {"type": "integer"}
            }
        }
    }
]
```

---

## 5. Agentループ（制御フロー）

```python
def run_agent(image_path: str, vlm: VLMClient, cv: CVToolkit, max_steps: int = 5):
    messages = [{"role": "user", "content": "この画像の透視法を判定し、消失点を検出してください。"}]
    state = {"lines": None, "vps": None, "attempts": 0}

    for step in range(max_steps):
        response = vlm.classify_and_act(image_path, messages, TOOLS)

        if not response.tool_calls:
            break  # VLMが最終回答をテキストで返した＝終了

        for call in response.tool_calls:
            result = dispatch_tool(call, cv, state)   # 実行結果をCVToolkitで計算
            messages.append({"role": "tool", "name": call.name, "content": result})

            # --- 数値的な妥当性チェック（Judge不要の自動判定） ---
            if call.name == "find_vanishing_points":
                if not is_valid_vp_result(result):
                    state["attempts"] += 1
                    if state["attempts"] >= 2:
                        return {"status": "failed", "reason": "検出失敗（再試行上限）"}
                    # 次のループでVLMに「結果が不十分」と伝え、再試行させる
                    messages.append({"role": "user", "content": "検出精度が低いようです。パラメータを調整して再試行してください。"})

    return state
```

### 妥当性チェックの基準（Judgeの代わり）
- `find_vanishing_points` の結果、クラスタ内の直線数が閾値未満（例: 3本未満）→ 失敗扱い
- 消失点座標が画像サイズに対して極端に遠い（例: 画像幅の50倍以上外側）→ 「実質平行」として再分類を促す
- `detect_lines` の検出本数が極端に少ない（例: 5本未満）→ Cannyパラメータを下げて自動リトライ

これらは全て機械的な数値比較で判定でき、LLMによる評価（Judge）を挟まない。

---

## 6. CVToolkit（幾何計算部分・プロバイダ非依存）

```python
class CVToolkit:
    def detect_lines(self, image, canny_low=50, canny_high=150) -> list[Line]:
        # Canny edge detection → LSD (Line Segment Detector)
        ...

    def find_vanishing_points(self, lines: list[Line], num_vps: int) -> list[VanishingPoint]:
        # 角度でクラスタリング → 各クラスタでRANSACによる交点推定
        ...

    def draw_overlay(self, image, vps: list[VanishingPoint]) -> Image:
        # 確認用に元画像に消失点・直線を重畳描画（VLMへの再提示用）
        ...
```

このレイヤーはVLMプロバイダに一切依存しないため、Gemma3→OpenAIの切り替え時も
無改修で流用できる。

---

## 7. ディレクトリ構成（最小構成案）

```
vp_agent/
├── config.yaml
├── main.py                 # Agentループ + CLI
├── vlm/
│   ├── base.py              # VLMClient (ABC), ToolCall, VLMResponse
│   ├── ollama_client.py      # OllamaVLMClient
│   └── openai_client.py      # OpenAIVLMClient
├── cv/
│   └── toolkit.py            # CVToolkit（detect_lines, find_vanishing_points等）
├── tools_schema.py           # 共通ツール定義（TOOLS）
└── validators.py             # is_valid_vp_result 等の妥当性チェック関数
```

---

## 8. 実装の進め方（推奨ステップ）

1. **CVToolkitを先に単体で完成させる**（VLM抜きで、決め打ちの透視法種別＋固定パラメータで
   `detect_lines` → `find_vanishing_points` が動くことを確認）
2. `VLMClient` の抽象基底クラスと `OllamaVLMClient` を実装し、`classify_perspective` の
   判定だけをまず動かす（ツール呼び出しは1個だけの最小構成）
3. Agentループに `detect_lines` / `find_vanishing_points` を組み込み、
   一点透視のみ対応する最小ループを完成させる
4. 妥当性チェック（`validators.py`）を実装し、再試行ループを動かす
5. 二点透視・三点透視への対応を拡張
6. 最後に `OpenAIVLMClient` を実装し、`config.yaml` の切り替えだけで
   同じAgentが動くことを確認する

---

## 9. 既知のリスク・注意点

- Gemma3:4bのtool calling精度が低い場合、`classify_perspective` の判定自体が
  不安定になる可能性がある → `confidence: low` を返させ、低確信度時は
  二点透視と三点透視の両方を試して結果を比較する、というフォールバックを
  Agentループ側に用意しておくと良い。
- 自然画像はノイズ直線（木、雲、人物輪郭等）が多く、Hough変換だけでは
  誤検出が増える。LSD + RANSACベースのクラスタリングを推奨。
- VLMにピクセル座標を直接答えさせるのは避ける（精度が出ない）。
  座標計算は必ずCVToolkit側に委ねる。
