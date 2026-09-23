# 建築図面PDFベクター情報抽出・分類基盤 仕様書

最終更新: 2026-09-23

## 1. 目的

ベクター描画された建築図面PDF（例: `resouces/設計図-2階平面詳細図.pdf`）から、線・矩形・曲線・文字などのベクター情報を抽出し、中間データ(JSON)として出力する（**抽出タスク**）。さらに、その中間データを解釈して図面要素(通り芯線・通り芯番号・寸法線・壁の中心線・引き出し線・扉開閉境界の円弧など)を分類する（**分類タスク**）。両タスクの結果は、原本との一致度を目視確認するためSVGへの再描画も行う。

**抽出タスクと分類タスクはコード上疎結合であり、それぞれ独立に実行できる**(詳細は`docs/adr/0005-extract-classify-decoupling.md`)。分類タスクは抽出タスクが出力したJSONのみを入力とし、PDFファイル・`pdfplumber`には一切依存しない。

## 2. 技術構成

- 言語/パッケージ管理: Python (`uv`使用、`pyproject.toml`に`[tool.uv] exclude-newer = "7 days"`を設定)
- PDF読み取り: `pdfplumber` (`pdfminer.six` + `pypdfium2`をラップ)
- 画素解析(線幅キャリブレーション用): `Pillow`
- プロジェクトレイアウト: `src/classifier_arch_drawing/` 配下にモジュールを配置する`src`レイアウト

## 3. モジュール構成

```
src/classifier_arch_drawing/
  schema.py            中間データの共通レコード形式(VectorRecord)を定義
  extract.py            pdfplumberの生データをVectorRecordに正規化して抽出
  calibration.py         線幅(linewidth)の自動キャリブレーション
  render.py              VectorRecordのリストからSVGを再描画(ハイライト機能含む)
  cli.py                 抽出タスクのCLIエントリポイント(`extract-vectors`)
  classify/
    __init__.py           分類ロジックの公開インターフェース
    geometry.py           円検出など共通の幾何ヘルパー
    grid.py               通り芯・通り芯番号の分類ロジック
    dimension.py          寸法線・寸法の分類ロジック
  classify_cli.py        分類タスクのCLIエントリポイント
                         (`classify-grid-lines` / `classify-dimension-lines`)
```

`classify/`配下と`classify_cli.py`は`schema.VectorRecord`という**データ型のみ**に依存し、`extract.py`・`pdfplumber`・PDFファイルをimportしない。この依存方向(`classify → schema`のみ、`classify → extract`は無し)によって、抽出タスクと分類タスクの疎結合を保っている。

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

`build_svg(records, page_width, page_height, object_types=(...), linewidth_scale=1.0, highlight_indices=None, highlight_color="#ff4500")`が中間データからSVG文字列を生成する。

`highlight_indices`(recordsリスト中のインデックス集合)を指定すると、該当レコードのみ`stroke`(line/rect/curve)または`fill`(char)を`highlight_color`で上書きする。それ以外のレコードは通常通りの色・線幅・回転で描画される。この仕組みは「通り芯」という概念を一切知らない汎用的なものであり、分類タスク側(`classify_cli.py`)が「どのインデックスをハイライトするか」を決めて渡す。

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

### 7.4 面の塗りつぶし(`FillPolygon`)

壁分類(13章)のような、スコアに応じたグラデーション塗りつぶしを表現するため、`build_svg`は`fill_polygons: list[FillPolygon] | None = None`という追加引数を持つ(デフォルト`None`で既存呼び出しは無変更)。

```python
@dataclass(frozen=True)
class FillPolygon:
    points: tuple[tuple[float, float], ...]
    color: str
    opacity: float = 0.5
```

`fill_polygons`が指定された場合、通常のstroke描画の後に`<polygon fill="{color}" fill-opacity="{opacity}">`要素を追加描画する。`render.py`は「ポリゴン+色+不透明度」のみを扱い、それが何を表すか(壁のスコア等)は一切関知しない(`highlight_indices`と同じ責務分離)。スコア値から色への変換は`_gradient_color(score_ratio, low_color, high_color)`が担い、RGB各チャンネルを線形補間する。スコア→比率(0〜1)への変換は呼び出し側(`classify_cli.py`)の責務。

## 8. 抽出タスクCLI (`cli.py`)

エントリポイント: `extract-vectors`(`pyproject.toml`の`[project.scripts]`で定義)

```
uv run extract-vectors <PDFパス> [-o 出力JSONパス] [--pretty] [--svg 出力SVGパス]
                        [--linewidth-scale <float>] [--no-linewidth-calibration]
```

| オプション | 説明 |
|---|---|
| `-o, --output` | 中間データをJSONファイルに出力(形式は8.1節参照) |
| `--pretty` | JSON出力をインデント付きで整形 |
| `--svg` | 再描画したSVGの出力先 |
| `--linewidth-scale <float>` | 線幅補正係数を手動指定し、自動キャリブレーションをスキップする |
| `--no-linewidth-calibration` | 自動キャリブレーションを無効化し、係数1.0(生の報告値のまま)を使用する |

`-o`または`--svg`のいずれかが指定された場合、1ページ目に対して線幅キャリブレーションを実行し、結果(`scale`, `method`, `samples=実測数/候補数`, `confidence`)を標準出力に表示する。

### 8.1 JSON出力形式

分類タスクがPDFを再度開かずに動作できるよう、`-o`で出力するJSONはレコードの配列だけでなく、ページ寸法とキャリブレーション結果を含むオブジェクトにしている。

```json
{
  "page_width": 1191,
  "page_height": 842,
  "linewidth_scale": 0.18,
  "records": [ { "object_type": "line", ... }, ... ]
}
```

(旧形式は`records`配列のみのフラットなリストだったが、分類タスクとの疎結合のためこの形式に変更した。詳細は`docs/adr/0005-extract-classify-decoupling.md`)

## 9. 分類タスク: 通り芯・通り芯番号 (`classify/grid.py`, `classify_cli.py`)

### 9.1 概要

抽出タスクのJSON出力(8.1節の形式)を読み込み、以下を分類する。

- **通り芯番号(`GridLabel`)**: 丸(`curve`)の中に数字/アルファベット(またはその組み合わせ)の`char`が入ったラベル
- **通り芯(`GridLine`)**: 通り芯番号の中心と同じ座標(水平ならy、垂直ならx)を共有する、ページの広い範囲にわたる`line`セグメントの集合

判定条件(詳細・実データでの検証結果は`docs/adr/0006-grid-line-classification.md`):

1. `curve`の点列(`pts`)の重心距離の相対標準偏差が小さい(≈真円)ものを円候補とする
2. 円の中心近傍にある`char`を連結し、`^[A-Za-z]{0,3}[0-9]{0,3}$`にマッチする(かつ非空の)ラベルのみを採用する
3. 円の中心と同じ座標を共有し、かつ**本物の一点鎖線の交互リズム(長い線・短い線が交互に続く)**を持つ`line`のラン(`classify/geometry.py`の`find_chain_runs`)が、ページ寸法に対して十分な範囲(既定: 該当軸のページ寸法の10%以上)にわたって存在する場合のみ、その円ラベルを「通り芯番号」として確定し、該当する線分群を「通り芯」として結び付ける(単に座標・本数が揃っているだけでなく、交互リズムそのものを検証することで、無関係な直線が偶然同じ座標に混入することを防ぐ)
4. ラベルテキストをアルファベット接頭辞と数値部分に分解し、同じ接頭辞を持つラベル間で数値部分に重複がないか(=連番としてもっともらしいか)を`sequence_plausible`として記録する(結果への参考情報であり、判定のフィルタ条件には使わない)

半径のような絶対値をハードコードせず、「妥当なラベルテキストの存在」と「対応する長い共線クラスタの存在」という2条件の組み合わせのみで判定することで、ドア把手・水栓アイコン・方位マーク等の装飾的な円との誤検出を避けている。

### 9.2 データ型

```python
@dataclass(frozen=True)
class GridLabel:
    text: str
    center: tuple[float, float]
    radius: float
    circle_index: int          # recordsリスト中のインデックス(丸のcurve)
    char_indices: tuple[int, ...]

@dataclass(frozen=True)
class GridLine:
    label: GridLabel
    axis: Literal["horizontal", "vertical"]
    coordinate: float
    segment_indices: tuple[int, ...]  # 通り芯を構成するlineのインデックス
    span: tuple[float, float]
    sequence_group: str
    sequence_number: int | None
    sequence_plausible: bool
```

### 9.3 分類タスクCLI (`classify_cli.py`)

エントリポイント: `classify-grid-lines`

```
uv run classify-grid-lines <抽出JSONパス> [-o 出力JSONパス] [--pretty] [--svg 出力SVGパス]
```

| オプション | 説明 |
|---|---|
| `-o, --output` | 検出した`GridLine`のリストをJSONファイルに出力 |
| `--pretty` | JSON出力をインデント付きで整形 |
| `--svg` | 通り芯・通り芯番号を`#ff4500`で着色した再現SVGの出力先(それ以外の要素は抽出タスクと同じ見た目で描画される) |

標準出力には検出した各`GridLine`のラベル・軸・線分本数・スパン・連番妥当性を表示する。

## 10. 分類タスク: 寸法線・寸法 (`classify/dimension.py`, `classify_cli.py`)

### 10.1 概要

抽出タスクのJSON出力を読み込み、以下を分類する。内部で`classify_grid_lines`を自動実行し、通り芯との位置関係も判定する。

- **寸法(数値ワード)**: 数字・カンマの`char`を読み方向でグルーピングして復元した文字列(例:「3,700」)
- **寸法線区間(`DimensionSegment`)**: 1つの数値ワードに対応する、端部同士が接続した実線の1区間

判定条件(詳細・実データでの検証結果は`docs/adr/0007-dimension-line-classification.md`):

1. `char`のうち`[0-9,]`にマッチするものを、`matrix`から求めた読み方向ベクトルへの射影でソートし、垂直座標でグルーピングした上でフォントサイズに比例した間隔閾値で隣接文字を連結して数値ワードを復元する。`^[0-9]+(,[0-9]+)*$`にマッチしないもの、桁数2未満(カンマ除く)のものは除外する
2. 軸並行(水平/垂直)の`line`のうち、**端点同士が接続していて(隙間がない)、かつ線幅が同一である**もの同士だけを連結成分としてグルーピングする(通り芯の一点鎖線は隙間だらけのため自然に除外される)。単一の実線だけで完結する区間(細分化されない総寸法線)も1つのネットワークとして扱う
3. 各数値ワードについて、読み方向に応じた直下/直脇(フォントサイズの1.5倍以内)にあり、かつ**数値ワードの中心が区間のほぼ中央(区間長の25%以内)に来る**区間を、中心のズレが最小のものから対応付ける(寸法値は寸法線のほぼ中央に記載されるという慣行を利用する)
4. 各区間の両端点について、その点を中心とする小さい円(端部マーカー、`classify/geometry.py`の円検出を再利用)を探索する。**両端に端部マーカーが実在しない場合はその対応付けを破棄する**(中央性基準だけでは、無関係な線に数値が偶然centeringしてしまう誤検出を排除しきれないため、寸法線には必ず端部の丸がある、という実体的な条件で最終確認する)
5. 各区間の両端点について、伸びる線を次の2種類の**和集合**として探索する(単発の直線はどちらにも該当しないため除外される):
   - **(a) 直接交差型**: 端部の点に直接交差し、かつ寸法線本体(端部同士をつなぐ線)と同一線幅を持つ線。「端部と直接交差してからちょっとだけ伸びている線」はこちらで拾う
   - **(b) 一点鎖線型**: 端部と同一座標(x/y)を共有し、通り芯分類(ADR0006)と同じ`find_chain_runs`で**本物の長短交互リズム**を検証した上でページ寸法の10%以上のスパンを持つラン。単なる座標一致・本数・スパンの統計だけでなく、実際に「長い線→短い線→長い線……」という交互パターンを検証するため、たまたま同じ座標・線幅の単発の直線(壁の引き通し線等)や無関係な記号の細部(窓記号「W2」近傍の短い線分)を「一点鎖線」として誤って採用しない。同一座標上に無関係な線が挟まっている場合はそこでランが分断され、挟まれた区間は採用されないが、その先で純粋な一点鎖線が続いていれば別のランとして検出される
6. 区間の端点座標が、`classify_grid_lines`が返す`GridLine`のうち軸が直交するものの座標と一致すれば`is_axis_derived=True`(通り芯由来)とする

「隙間のない連続実線」であることが寸法線を通り芯(隙間だらけの一点鎖線)と区別する主な判定基準であり、半径や絶対座標のハードコードは避けている。

### 10.2 データ型

```python
@dataclass(frozen=True)
class DimensionSegment:
    value_text: str                      # 例: "3,700"
    value_char_indices: tuple[int, ...]
    axis: Literal["horizontal", "vertical"]
    coordinate: float
    line_indices: tuple[int, ...]
    start_point: tuple[float, float]
    end_point: tuple[float, float]
    start_marker_index: int | None        # 端部の丸(curve)のインデックス
    end_marker_index: int | None
    start_extension_indices: tuple[int, ...]  # 端部から伸びる線
    end_extension_indices: tuple[int, ...]
    is_axis_derived: bool                 # 通り芯由来かどうか
```

### 10.3 分類タスクCLI

エントリポイント: `classify-dimension-lines`

```
uv run classify-dimension-lines <抽出JSONパス> [-o 出力JSONパス] [--pretty] [--svg 出力SVGパス]
```

| オプション | 説明 |
|---|---|
| `-o, --output` | 検出した`DimensionSegment`のリストをJSONファイルに出力 |
| `--pretty` | JSON出力をインデント付きで整形 |
| `--svg` | 寸法線を`#008000`で着色した再現SVGの出力先(通り芯分類のSVGとは別ファイル) |

標準出力には検出した各`DimensionSegment`の数値・軸・始終点・通り芯由来かどうかを表示する。

## 11. 分類タスク: 壁(内壁) (`classify/wall.py`, `classify_cli.py`)

### 11.1 概要

抽出タスクのJSON出力を読み込み、内壁(外壁は対象外)を分類する。内部で`classify_grid_lines`・`classify_dimension_lines`を自動実行し、通り芯・寸法線を壁候補から除外した上で、寸法線との位置関係をスコアの一根拠として使う。

壁は図面の詳細度によって描き方が変わり、通り芯・寸法線のような決定的な条件だけでは判定しきれないため、**候補生成(Phase A)+スコアリング(Phase B)**の2段構成を取る(詳細・実データでの検証結果・既知の制約は`docs/adr/0008-wall-classification.md`を参照)。

**候補生成**:

1. 軸並行(水平/垂直)の`line`を線幅ごとにグループ化し、同一座標に密集する断片を1本の「面」にまとめる
2. 通り芯・寸法線を構成する`line`は候補から除外する
3. 各面を、座標が大きい側で最初に条件(同一線幅・十分な重なり・壁厚として妥当な間隔)を満たす面と**nearest-neighbor方式で1対1にペア化**する(`WallPairSegment`)
4. 壁厚の上限は、ページ対角線比の粗い上限と、間隔分布の自然な倍率ジャンプの両方で絞り込む
5. 「壁は必ず閉じた直線(部屋を一周する輪郭)に囲まれている」という制約を、センターライン端点グラフに対する**leaf-pruning(2-core抽出)**として適用し、他の候補と繋がって閉ループを構成しない(行き止まりの)候補を除外する
6. 閉ループを構成する候補を、方向転換を許容して連結し`WallRun`とする

**スコアリング**(`WallRun`ごとに加点、詳細はADR0008):

- `dimension_symmetry`: 寸法線の中心点、または端部からの直交方向仮想延長線とセンターラインが一致するか
- `room_enclosure`: 部屋名文字列(「室」「ルーム」「トイレ」「便所」「オフィス」「ホール」「廊下」「場」「エリア」「PS」「EPS」等)から四方にレイキャストしてヒットするか
- `door_adjacency`: ドア記号(開いた円弧、`find_arcs`)の中心が壁センターライン付近にあるか(**本PDFでは実質機能していない。既知の制約参照**)
- `wall_label`: 壁符号(「W1」等)・「壁」という文字列が近傍にあるか
- `parallel_continuity`: 連続長・厚みの安定性(候補生成の基準でもある)

合計スコアが、スコア分布の自然な倍率ジャンプから導出した閾値(境界が見つからない場合はフォールバック定数)以上であれば壁と判定する。

### 11.2 データ型

```python
@dataclass(frozen=True)
class WallPairSegment:
    axis: Literal["horizontal", "vertical"]
    coordinate_a: float
    coordinate_b: float
    lo: float
    hi: float
    line_indices_a: tuple[int, ...]
    line_indices_b: tuple[int, ...]
    linewidth: float | None
    thickness: float

@dataclass(frozen=True)
class WallRun:
    segments: tuple[WallPairSegment, ...]
    line_indices: tuple[int, ...]
    polygon: tuple[tuple[float, float], ...]
    score: float
    score_components: dict[str, float]
    is_wall: bool
```

### 11.3 分類タスクCLI

エントリポイント: `classify-wall-lines`

```
uv run classify-wall-lines <抽出JSONパス> [-o 出力JSONパス] [--pretty] [--svg 出力SVGパス]
```

| オプション | 説明 |
|---|---|
| `-o, --output` | 検出した`WallRun`(`is_wall=True`のみ)のリストをJSONファイルに出力 |
| `--pretty` | JSON出力をインデント付きで整形 |
| `--svg` | 壁候補をスコアに応じたグラデーション(低: `#66cdaa` 〜 高: `#ff69b4`、不透明度50%)で塗りつぶした再現SVGの出力先 |

標準出力には検出した各`WallRun`のスコア・スコア内訳・構成セグメント数を表示する。

### 11.4 既知の制約(初版・2026-09-23時点)

このタスクは通り芯・寸法線分類と異なり、実データでの精度検証が途上である。**再現率(検出漏れ)が低いことが判明している**(本PDFでの検証では、閉ループ制約により誤検出は解消できたが、壁候補953件中、最終的に壁と判定されたのは6件のみで、明らかに壁と分かる建物外周・室内間仕切りの大半を検出できていない)。またドア記号検出(`find_arcs`)は本PDFでは装飾的な小さい円弧しか検出できず、実質機能していない。詳細な原因分析・今後の改善方針は`docs/adr/0008-wall-classification.md`の「既知の制約」節を参照。

## 12. 検証方法

### 抽出タスク

1. `uv run extract-vectors resouces/設計図-2階平面詳細図.pdf -o output/設計図-2階平面詳細図.json --svg output/検出線.svg`を実行し、標準出力の集計統計・キャリブレーション結果、および出力JSONが8.1節の形式であることを確認する
2. 生成されたSVGを`rsvg-convert`等でラスタライズし、`page.to_image()`で得た原本のラスタ画像と同一DPI・同一ピクセル座標で比較する
   - 太さ検証: 特定のx/y座標での水平・垂直スキャンラインを取り、連続する黒画素のラン長(px)を実測して原本と比較する
   - 回転文字検証: 斜め文字・縦書き数字の該当領域を切り出し、向き・サイズが原本と一致するか目視確認する
3. `--no-linewidth-calibration`指定時に係数が1.0になり、自動キャリブレーション実行時と結果が異なることを確認する

### 分類タスク

1. `uv run classify-grid-lines output/設計図-2階平面詳細図.json -o output/通り芯分類結果.json --svg output/通り芯分類.svg --pretty`を実行する
2. 出力JSONで、`X1`/`X2`/`Y1`/`Y2`の4つの`GridLine`が検出され、装飾的な円が誤検出されていないことを確認する
3. 出力SVGをラスタライズし、通り芯・通り芯番号(丸+文字+線)が`#ff4500`で着色され、それ以外の要素は抽出タスクと同じ見た目で再現されていることを目視確認する
4. `classify/`配下および`classify_cli.py`のimportに`extract`や`pdfplumber`が含まれていないことを確認し、抽出タスクと分類タスクが疎結合であることを確認する(`classify/dimension.py`が`classify/grid.py`をimportするのは分類タスク内部の依存として許容する)
5. `uv run classify-dimension-lines output/設計図-2階平面詳細図.json -o output/寸法線分類結果.json --svg output/寸法線分類.svg --pretty`を実行し、主要な寸法チェーン(例: 上辺の「3,700/1,800/2,900/1,200」、下辺の「950/1,900/1,750/1,900/1,900」等)が正しい数値・`is_axis_derived`で検出されることを確認する
6. 出力SVGをラスタライズし、寸法線(実線・端部の丸・伸びる線)が`#008000`で着色され、それ以外の要素(壁・通り芯・テキスト等)は従来通り再現されていることを目視確認する

### 壁分類

1. `uv run classify-wall-lines output/設計図-2階平面詳細図.json -o output/壁分類結果.json --svg output/壁分類.svg --pretty`を実行する
2. 出力SVGをラスタライズし、フローリング材のハッチング・通り芯・寸法線が壁として誤って塗りつぶされていないことを確認する(誤検出防止は検証済み。検出漏れが多いことは既知の制約として11.4節に記載)
3. `classify-grid-lines`/`classify-dimension-lines`を再実行し、`geometry.py`の共通ヘルパー化(`collect_axis_aligned_lines`, `collect_char_runs`, `split_by_relative_jump`)により既存の検出結果(通り芯4本、寸法線36件)が変化していないことを確認する

## 13. 今後の課題(未着手)

- **壁分類の再現率向上**: 壁候補のnearest-neighborペア化が、実際の壁の対向面に到達する前に装飾的なtick線・仕上げ表現に阻まれるケースが多く、検出漏れが多い(詳細は`docs/adr/0008-wall-classification.md`)。次回優先して取り組むべき課題
- **ドア記号(扇形)検出の実データ検証**: `find_arcs`は円フィット自体は機能するが、本PDFでは装飾的な小さい円弧しか検出できていない
- 引き出し線等、通り芯・寸法線・壁以外の図面要素分類の実装
- 寸法線分類の閾値(`_WORD_TO_LINE_GAP_FACTOR`等)は本PDFで経験的に調整した値であり、他のPDFでの再調整が必要になる可能性がある
- 埋め込みフォントのSVGへの埋め込み対応(文字位置・字形の完全再現)
- 複数ページPDFへの対応(現状CLIの`--svg`は1ページ目のみを対象)
