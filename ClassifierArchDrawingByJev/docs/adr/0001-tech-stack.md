# ADR 0001: Pythonプロジェクト管理はuv、PDF読み取りはpdfplumberを採用する

- ステータス: 採用
- 日付: 2026-09-23

## コンテキスト

建築図面PDF(ベクター描画)からベクター情報(線分・矩形・曲線・文字)を取得し、後段の図面要素分類で扱える中間データとして出力する基盤を新規に構築する必要があった。プロジェクトはPythonとして未初期化(pyproject.toml/uv.lock無し)の状態だった。

## 決定

- Pythonのパッケージ管理・仮想環境構築には`uv`を使う(`uv init --lib` + `uv add`)
- 新規`pyproject.toml`の`[tool.uv]`に`exclude-newer = "7 days"`を設定する
- PDFのベクター情報読み取りには`pdfplumber`を使う(`pdfminer.six` + `pypdfium2`をラップしたライブラリ)
- 依存関係は第一弾では`pdfplumber`のみを追加する。`numpy`/`shapely`等の幾何演算ライブラリは、「抽出してJSON出力する」という第一弾のゴールには不要なため、分類ロジック実装フェーズで必要になった時点で追加する

## 理由

- `uv`/`pdfplumber`はユーザー指定の制約であり、代替ライブラリの比較検討は行っていない
- pdfplumberは`page.lines`/`page.rects`/`page.curves`/`page.chars`という形で、線・矩形・曲線・文字それぞれの属性(座標、線幅、色、フォント情報等)にアクセスできるAPIを提供しており、生のcontent streamを自前でパースするより開発コストが大幅に低い
- 過剰な先取り設計(将来使うかもしれないライブラリを最初から入れる)を避け、実際に必要になった時点で依存を追加する方針とした

## 影響

- `pyproject.toml`の`dependencies`は`pdfplumber`から始まり、その後の機能追加(線幅キャリブレーション)で`pillow`が明示的に追加された([0003](0003-linewidth-calibration.md)参照)
- プロジェクトレイアウトは`src/classifier_arch_drawing/`のsrcレイアウトを採用し、`uv init --lib`で生成した
