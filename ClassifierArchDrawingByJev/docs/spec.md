# 建築図面PDFベクター情報抽出・再描画基盤 仕様書

最終更新: 2026-09-23

## 1. 目的

ベクター描画された建築図面PDF（例: `resouces/設計図-2階平面詳細図.pdf`）から、線・矩形・曲線・文字などのベクター情報を抽出し、後段の図面要素分類（通り芯線・寸法線・壁の中心線・引き出し線・扉開閉境界の円弧・通り芯番号など）で扱いやすい中間データ(JSON)として出力する。また、抽出結果の正しさを目視確認するため、抽出データからSVGへの再描画を行う。

分類ロジック自体は本仕様の範囲外（次フェーズ）であり、本仕様は「PDF→中間データ→SVG再描画による検証」までを対象とする。

## 2. 技術構成

- 言語/パッケージ管理: Python (`uv`使用、`pyproject.toml`に`[tool.uv] exclude-newer = "7 days"`を設定)
- PDF読み取り: `pdfplumber` (`pdfminer.six` + `pypdfium2`をラップ)
- 画素解析(線幅キャリブレーション用): `Pillow`
- プロジェクトレイアウト: `src/classifier_arch_drawing/` 配下にモジュールを配置する`src`レイアウト

## 3. モジュール構成

```
src/classifier_arch_drawing/
  schema.py        中間データの共通レコード形式(VectorRecord)を定義
  extract.py        pdfplumberの生データをVectorRecordに正規化して抽出
  calibration.py     線幅(linewidth)の自動キャリブレーション
  render.py          VectorRecordのリストからSVGを再描画
  cli.py             上記を束ねるCLIエントリポイント(`extract-vectors`)
```

## 4. データモデル: `VectorRecord`

`schema.py`で定義する`TypedDict`。`line`/`rect`/`curve`/`char`の4種類のpdfplumberオブジェクトを共通形式に正規化する。

| フィールド | 型 | 説明 |
|---|---|---|
| `page_number` | `int` | ページ番号 |
| `object_type` | `str` | `"line"` \| `"rect"` \| `"curve"` \| `"char"` |
| `x0`,`x1`,`top`,`bottom`,`width`,`height` | `float` | 軸並行バウンディングボックス(pdfplumber座標系、top-down) |
| `linewidth` | `float \| None` | 報告された線幅(line/rect/curve用、後述の注意点あり) |
| `stroking_color` | `Any` | 線の色(グレースケール float、または RGB/CMYK tuple) |
| `non_stroking_color` | `Any` | 塗り色(char/curveの塗り用) |
| `dash` | `dict \| None` | 破線パターン情報(本PDFでは常に`None`。破線は複数の短い実線セグメントとして展開されている) |
| `fill` | `bool \| None` | 塗りつぶしの有無 |
| `pts` | `list[tuple[float,float]] \| None` | 経路の点列(line/rect/curve用) |
| `path` | `list \| None` | moveto/lineto/curvetoオペレータ付きの完全パス(curve用) |
| `text`,`fontname`,`size` | char用 | 文字・フォント名・pdfplumber報告のフォントサイズ(**回転文字では不正確、後述**) |
| `matrix` | `tuple[float,...,6] \| None` | 文字の変換行列`(a,b,c,d,e,f)`(char用。位置・回転・真のフォントサイズの算出に使用) |

## 5. 抽出処理 (`extract.py`)

- `extract_page_vectors(page)`: 1ページ分の`page.lines`/`page.rects`/`page.curves`/`page.chars`をそれぞれ`VectorRecord`へ正規化して結合する
- `extract_document_vectors(pdf_path, pages=None)`: 全ページ(または指定ページ)を走査して結合する
- `summarize(records)`: 種類別件数、`dash`非None件数、`linewidth`ユニーク値、色数、ベジェ制御点を含む曲線数、フォント名別文字数を集計する（検証・デバッグ用）

### 既知のデータ特性（本PDFでの実測結果）

- `page.rects`は0件。矩形もすべて`line`または`curve`として表現されている
- `dash`は全レコードで`None`。破線・一点鎖線は複数の短い実線セグメントとして展開されている（CADエクスポータの特性）
- `curve`はすべて点列近似(`pts`)であり、ベジェ制御点(`path`中の`c`オペレータ)を持つものは0件

## 6. 線幅の自動キャリブレーション (`calibration.py`)

### 6.1 背景

pdfplumberが報告する`linewidth`は、PDFの作成ソフトによっては実際にPDFビューアで描画される太さと一致しないことがある。本PDFでは、`page.to_image()`(pypdfium2による実際の描画結果)と比較すると、報告値が実測太さの約8倍になっていることを実測で確認した(詳細は`docs/adr/0003-linewidth-calibration.md`)。

これはPDFごとに異なりうる問題のため、**固定の補正係数をハードコードするのではなく、PDFごとに自動検出する**方式を採用している。

### 6.2 アルゴリズム概要

1. `page.to_image()`のラスタ画像を「正解」とみなす
2. 抽出済みレコードから「孤立した直線サンプル」を選定する
   - 対象: `line`/`rect`を分解した線分のうち、軸並行(±1度以内)
   - 長さ閾値: ページ対角線の0.5%（下限5pt）
   - 孤立判定: 自身の`linewidth`(最低1pt)を余白としたバウンディングボックスに、他のどのレコードとも重なりがないこと
   - 報告`linewidth`の値ごとに層化サンプリング(上限60本)
3. 各サンプルについて、線分の中点を通る垂線方向にラスタ画像をスキャンし、背景色でなくなる連続画素幅(px)を実測する
4. `実測pt / 報告linewidth` の比をサンプルごとに求め、中央値+MAD(median absolute deviation)による外れ値除去を行い、頑健なスケール係数を1つ算出する
5. サンプル数が不足する場合(distinct linewidth値の種類数から算出する最低本数に満たない場合)は`scale=1.0`にフォールバックする

### 6.3 `CalibrationResult`

```python
@dataclass(frozen=True)
class CalibrationResult:
    scale: float          # 報告linewidth→実測ptへの乗数
    sample_count: int     # 実際に測定できたサンプル数
    candidate_count: int  # 孤立線候補として検出した数
    per_sample_ratios: list[float]
    method: Literal["measured", "fallback_no_samples", "fallback_disabled", "manual"]
    confidence: float     # 0.0-1.0
```

通常のPDF(報告値と実測が一致するケース)では自動的に`scale≈1.0`になり無害。本PDFでは`scale≈0.18`前後を自動検出する(手動で導出した経験値`1/8=0.125`と近い値)。

## 7. SVG再描画 (`render.py`)

`build_svg(records, page_width, page_height, object_types=(...), linewidth_scale=1.0)`が中間データからSVG文字列を生成する。

### 7.1 線・矩形・曲線

- `pts`があればそのままpolylineとして描画、なければ`x0/x1/top/bottom`から単純な`<line>`にフォールバック
- 線幅: `linewidth * linewidth_scale`(最小0.15ptの安全弁あり)
- 色: `stroking_color`をCSS色(グレースケール/RGB/CMYK対応)に変換して`stroke`に反映

### 7.2 文字

文字の位置・回転・フォントサイズは、**`matrix`(変換行列 `a,b,c,d,e,f`)から直接導出する**。バウンディングボックス由来の`x0`/`bottom`/`size`は信頼できないケースがあるため、フォールバック用途にのみ使う。

- 有効フォントサイズ: `font_size = sqrt(a² + b²)`
- 回転角(SVG用、度): `rotation_deg = -degrees(atan2(b, a))`
- 描画位置: `x = e`, `y = page_height - f`
- SVG出力: `<text x="{x}" y="{y}" font-size="{font_size}" transform="rotate({rotation_deg} {x} {y})">`(回転角がほぼ0の場合は`transform`属性を省略)
- `matrix`が取得できない場合のみ、`x0`/`bottom`/`size`・回転なしにフォールバックする

この方式により、以下の問題を統一的に解決している(詳細は`docs/adr/0004-char-matrix-rendering.md`):

- 斜めに描画された文字列(例:「隣地境界線」)が一文字ずつ水平に並ぶのではなく、正しい向きで連続して表示される
- 縦書きの寸法数字が正しい向き・サイズで表示される(`size`フィールドは回転文字で不正確になることを実測で確認済み。回転90°の文字で報告値が真値のちょうど半分になるケースを確認)
- 通り芯番号(`X1`/`X2`/`Y1`/`Y2`等)の位置ズレが軽減される(バウンディングボックス下端ではなく真のベースラインを使うようになったため)

### 7.3 既知の制約(未解決・保留事項)

- SVGは実際の埋め込みフォント(`AAAAAB+font0000000030638abc`等)ではなく代替フォント(`font-family="sans-serif"`)で描画している。そのため、グリフの字形・字送り幅が原本と完全には一致せず、通り芯番号などの文字位置にわずかな残差が残る。フォント埋め込み対応は別タスクとして保留している。

## 8. CLI (`cli.py`)

エントリポイント: `extract-vectors`(`pyproject.toml`の`[project.scripts]`で定義)

```
uv run extract-vectors <PDFパス> [-o 出力JSONパス] [--pretty] [--svg 出力SVGパス]
                        [--linewidth-scale <float>] [--no-linewidth-calibration]
```

| オプション | 説明 |
|---|---|
| `-o, --output` | 中間データ(全`VectorRecord`)をJSONファイルに出力 |
| `--pretty` | JSON出力をインデント付きで整形 |
| `--svg` | 再描画したSVGの出力先。指定時は1ページ目に対して線幅キャリブレーションを実行する |
| `--linewidth-scale <float>` | 線幅補正係数を手動指定し、自動キャリブレーションをスキップする |
| `--no-linewidth-calibration` | 自動キャリブレーションを無効化し、係数1.0(生の報告値のまま)を使用する |

`--svg`実行時は、キャリブレーション結果(`scale`, `method`, `samples=実測数/候補数`, `confidence`)を標準出力に表示する。

## 9. 検証方法

1. `uv run extract-vectors resouces/設計図-2階平面詳細図.pdf --svg output/検出線.svg`を実行し、標準出力の集計統計・キャリブレーション結果を確認する
2. 生成されたSVGを`rsvg-convert`等でラスタライズし、`page.to_image()`で得た原本のラスタ画像と同一DPI・同一ピクセル座標で比較する
   - 太さ検証: 特定のx/y座標での水平・垂直スキャンラインを取り、連続する黒画素のラン長(px)を実測して原本と比較する
   - 回転文字検証: 斜め文字・縦書き数字の該当領域を切り出し、向き・サイズが原本と一致するか目視確認する
3. `--no-linewidth-calibration`指定時に係数が1.0になり、自動キャリブレーション実行時と結果が異なることを確認する

## 10. 今後の課題(未着手)

- 分類ロジック本体(通り芯線・寸法線・壁中心線などへの分類)の実装
- 埋め込みフォントのSVGへの埋め込み対応(文字位置・字形の完全再現)
- 複数ページPDFへの対応(現状CLIの`--svg`は1ページ目のみを対象)
