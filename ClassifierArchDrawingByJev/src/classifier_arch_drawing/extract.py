"""PDFからベクターオブジェクト（線・矩形・曲線・文字）を抽出する。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pdfplumber

from .schema import VectorRecord


def open_pdf(pdf_path: Path) -> pdfplumber.PDF:
    return pdfplumber.open(pdf_path)


def _base_fields(obj: dict[str, Any], object_type: str) -> VectorRecord:
    return {
        "page_number": obj.get("page_number"),
        "object_type": object_type,
        "x0": obj.get("x0"),
        "x1": obj.get("x1"),
        "top": obj.get("top"),
        "bottom": obj.get("bottom"),
        "width": obj.get("width"),
        "height": obj.get("height"),
    }


def _normalize_stroke(obj: dict[str, Any], object_type: str) -> VectorRecord:
    record = _base_fields(obj, object_type)
    record["linewidth"] = obj.get("linewidth")
    record["stroking_color"] = obj.get("stroking_color")
    record["non_stroking_color"] = obj.get("non_stroking_color")
    record["dash"] = obj.get("dash")
    record["fill"] = obj.get("fill")
    record["pts"] = obj.get("pts")
    record["path"] = obj.get("path")
    return record


def _normalize_line(obj: dict[str, Any]) -> VectorRecord:
    return _normalize_stroke(obj, "line")


def _normalize_rect(obj: dict[str, Any]) -> VectorRecord:
    return _normalize_stroke(obj, "rect")


def _normalize_curve(obj: dict[str, Any]) -> VectorRecord:
    return _normalize_stroke(obj, "curve")


def _normalize_char(obj: dict[str, Any]) -> VectorRecord:
    record = _base_fields(obj, "char")
    record["text"] = obj.get("text")
    record["fontname"] = obj.get("fontname")
    record["size"] = obj.get("size")
    record["matrix"] = obj.get("matrix")
    return record


def extract_page_vectors(page: pdfplumber.page.Page) -> list[VectorRecord]:
    records: list[VectorRecord] = []
    records.extend(_normalize_line(o) for o in page.lines)
    records.extend(_normalize_rect(o) for o in page.rects)
    records.extend(_normalize_curve(o) for o in page.curves)
    records.extend(_normalize_char(o) for o in page.chars)
    return records


def extract_document_vectors(
    pdf_path: Path, pages: list[int] | None = None
) -> list[VectorRecord]:
    records: list[VectorRecord] = []
    with open_pdf(pdf_path) as pdf:
        target_pages = (
            pdf.pages
            if pages is None
            else [pdf.pages[i] for i in pages]
        )
        for page in target_pages:
            records.extend(extract_page_vectors(page))
    return records


def summarize(records: list[VectorRecord]) -> dict[str, Any]:
    type_counts = Counter(r["object_type"] for r in records)

    dash_by_type: Counter[str] = Counter()
    linewidths: set[float] = set()
    colors: set[Any] = set()
    curve_with_bezier = 0
    fontnames: Counter[str] = Counter()

    for r in records:
        if r.get("dash"):
            dash_by_type[r["object_type"]] += 1
        if r.get("linewidth") is not None:
            linewidths.add(r["linewidth"])
        if r.get("stroking_color") is not None:
            colors.add(r["stroking_color"])
        if r["object_type"] == "curve" and r.get("path"):
            if any(cmd[0] == "c" for cmd in r["path"]):
                curve_with_bezier += 1
        if r["object_type"] == "char" and r.get("fontname"):
            fontnames[r["fontname"]] += 1

    return {
        "total_records": len(records),
        "counts_by_object_type": dict(type_counts),
        "dash_non_null_by_object_type": dict(dash_by_type),
        "linewidth_unique_values": sorted(linewidths),
        "stroking_color_unique_count": len(colors),
        "curve_with_bezier_control_points": curve_with_bezier,
        "char_fontnames": dict(fontnames),
    }
