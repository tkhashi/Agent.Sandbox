# ADR 0002: line/rect/curve/charを共通のVectorRecord形式に正規化する

- ステータス: 採用
- 日付: 2026-09-23

## コンテキスト

pdfplumberは`page.lines`/`page.rects`/`page.curves`/`page.chars`という、オブジェクト種別ごとに異なるキー構成の辞書のリストを返す。これらを後段の分類ロジック(通り芯線・寸法線・壁中心線などへの分類)や、検証用のSVG再描画で扱いやすくする必要があった。

## 決定

`schema.py`に`VectorRecord`(`TypedDict`, `total=False`)を定義し、4種類のオブジェクトすべてを1つの共通フィールドセットに正規化する(`extract.py`の`_normalize_line`/`_normalize_rect`/`_normalize_curve`/`_normalize_char`)。

- 共通フィールド: `page_number`, `object_type`, `x0/x1/top/bottom/width/height`
- 線・矩形・曲線用: `linewidth`, `stroking_color`, `non_stroking_color`, `dash`, `fill`, `pts`, `path`
- 文字用: `text`, `fontname`, `size`, `matrix`(後から追加、[0004](0004-char-matrix-rendering.md)参照)
- 存在しないキーは`None`で埋め、pdfplumberのバージョン差異・オブジェクト種別差異を吸収する

## 理由

- 分類ロジックが「オブジェクトの種類によらず共通のインターフェースで走査できる」ことを優先した。種類ごとに個別の型を作ると、分類ロジック側で種類ごとの分岐が増え、拡張性が下がる
- `TypedDict(total=False)`を採用し、フィールドの有無をオプショナルにすることで、pdfplumberのバージョンやオブジェクト種別によってキーが欠けるケースにも安全に対応できるようにした
- 中間データをJSONとしてそのまま永続化できるようにし、次フェーズ(分類ロジック)の入力データとして再利用可能にした

## 影響

- 新しい属性(例: 文字の`matrix`)が必要になった場合は、`schema.py`にフィールドを追加し、`extract.py`の該当する`_normalize_*`関数で値を詰めるだけで済む拡張性を持つ
- `summarize()`(`extract.py`)はこの共通形式を前提に、種類別件数や属性の分布を集計している
