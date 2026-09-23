"""抽出済みベクターデータ(VectorRecord)を解釈する分類ロジック群。

このパッケージは`schema.VectorRecord`というデータ型のみに依存し、
`extract.py`・`pdfplumber`・PDFファイルには一切依存しない
(抽出タスクと分類タスクの疎結合を保つため)。
"""

from __future__ import annotations

from .dimension import DimensionSegment, classify_dimension_lines
from .grid import GridLabel, GridLine, classify_grid_lines
from .wall import WallPairSegment, WallRun, classify_wall_lines

__all__ = [
    "GridLabel",
    "GridLine",
    "classify_grid_lines",
    "DimensionSegment",
    "classify_dimension_lines",
    "WallPairSegment",
    "WallRun",
    "classify_wall_lines",
]
