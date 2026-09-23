"""PDFから抽出したベクターオブジェクトの共通レコード形式。"""

from __future__ import annotations

from typing import Any, TypedDict


class VectorRecord(TypedDict, total=False):
    page_number: int
    object_type: str  # "line" | "rect" | "curve" | "char"

    x0: float
    x1: float
    top: float
    bottom: float
    width: float
    height: float

    linewidth: float | None
    stroking_color: Any
    non_stroking_color: Any
    dash: dict[str, Any] | None
    fill: bool | None

    pts: list[tuple[float, float]] | None
    path: list[Any] | None

    text: str | None
    fontname: str | None
    size: float | None
    matrix: tuple[float, float, float, float, float, float] | None
