"""抽出したベクターオブジェクトをSVGとして再描画し、検出結果を目視確認する。"""

from __future__ import annotations

import math
from typing import Any

from .schema import VectorRecord

_DEFAULT_STROKE = "black"
_DEFAULT_FILL = "black"

# pdfplumberが報告する`linewidth`は、PDFによっては実際の描画太さと
# 一致しないことがある(calibration.calibrate_linewidthが検出するスケール差)。
# ここでは呼び出し側から渡された`scale`をそのままかけるだけにし、
# 補正係数そのものの導出はcalibration.py側の責務とする。
_MIN_LINEWIDTH = 0.15


def _effective_linewidth(raw: float | None, scale: float) -> float:
    if raw is None:
        return 0.5
    return max(raw * scale, _MIN_LINEWIDTH)


def _clamp255(v: float) -> int:
    return max(0, min(255, round(v * 255)))


def _color_to_css(color: Any, default: str) -> str:
    """pdfplumberの色表現(グレースケール/RGB/CMYK)をCSS色文字列に変換する。"""
    if color is None:
        return default

    if isinstance(color, (int, float)):
        g = _clamp255(color)
        return f"rgb({g},{g},{g})"

    if isinstance(color, (tuple, list)):
        if len(color) == 1:
            g = _clamp255(color[0])
            return f"rgb({g},{g},{g})"
        if len(color) == 3:
            r, g, b = color
            return f"rgb({_clamp255(r)},{_clamp255(g)},{_clamp255(b)})"
        if len(color) == 4:
            c, m, y, k = color
            r = 1 - min(1, c + k)
            g = 1 - min(1, m + k)
            b = 1 - min(1, y + k)
            return f"rgb({_clamp255(r)},{_clamp255(g)},{_clamp255(b)})"

    return default


def _points_to_polyline_points(pts: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)


def _char_transform(
    record: VectorRecord, page_height: float
) -> tuple[float, float, float, float] | None:
    """charの`matrix`(a,b,c,d,e,f)から(x, y, font_size, rotation_deg)を導出する。

    バウンディングボックス由来のx0/bottom/sizeは、回転した文字(斜め文字・
    縦書き数字)では信頼できない(実測でsizeが真の値の半分になる等の
    不整合を確認済み)ため、常にmatrixそのものから位置・回転・サイズを
    再計算する。
    """
    matrix = record.get("matrix")
    if not matrix:
        return None
    a, b, _c, _d, e, f = matrix
    font_size = math.hypot(a, b)
    if font_size <= 0:
        return None
    rotation_deg = -math.degrees(math.atan2(b, a))
    x = e
    y = page_height - f
    return x, y, font_size, rotation_deg


def _record_to_svg_element(
    record: VectorRecord, linewidth_scale: float, page_height: float
) -> str | None:
    object_type = record["object_type"]
    linewidth = _effective_linewidth(record.get("linewidth"), linewidth_scale)
    stroke = _color_to_css(record.get("stroking_color"), _DEFAULT_STROKE)

    if object_type in ("line", "rect"):
        pts = record.get("pts")
        if pts and len(pts) >= 2:
            points = _points_to_polyline_points(pts)
            return (
                f'<polyline points="{points}" fill="none" '
                f'stroke="{stroke}" stroke-width="{linewidth}" />'
            )
        x0, x1 = record.get("x0"), record.get("x1")
        top, bottom = record.get("top"), record.get("bottom")
        if None in (x0, x1, top, bottom):
            return None
        return (
            f'<line x1="{x0:.3f}" y1="{top:.3f}" x2="{x1:.3f}" y2="{bottom:.3f}" '
            f'stroke="{stroke}" stroke-width="{linewidth}" />'
        )

    if object_type == "curve":
        pts = record.get("pts")
        if not pts or len(pts) < 2:
            return None
        points = _points_to_polyline_points(pts)
        return (
            f'<polyline points="{points}" fill="none" '
            f'stroke="{stroke}" stroke-width="{linewidth}" />'
        )

    if object_type == "char":
        text = record.get("text")
        if not text or text.isspace():
            return None
        fill = _color_to_css(record.get("non_stroking_color"), _DEFAULT_FILL)
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

        transform = _char_transform(record, page_height)
        if transform is not None:
            x, y, font_size, rotation_deg = transform
        else:
            x, y = record.get("x0"), record.get("bottom")
            font_size = record.get("size") or 10
            rotation_deg = 0.0
            if None in (x, y):
                return None

        rotate_attr = (
            f' transform="rotate({rotation_deg:.3f} {x:.3f} {y:.3f})"'
            if abs(rotation_deg) > 1e-3
            else ""
        )
        return (
            f'<text x="{x:.3f}" y="{y:.3f}" font-size="{font_size:.3f}" '
            f'font-family="sans-serif" fill="{fill}"{rotate_attr}>{escaped}</text>'
        )

    return None


def build_svg(
    records: list[VectorRecord],
    page_width: float,
    page_height: float,
    object_types: tuple[str, ...] = ("line", "rect", "curve", "char"),
    linewidth_scale: float = 1.0,
) -> str:
    elements: list[str] = []
    for record in records:
        if record["object_type"] not in object_types:
            continue
        element = _record_to_svg_element(record, linewidth_scale, page_height)
        if element is not None:
            elements.append(element)

    body = "\n".join(elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{page_width:.3f}" height="{page_height:.3f}" '
        f'viewBox="0 0 {page_width:.3f} {page_height:.3f}">\n'
        f'<rect x="0" y="0" width="{page_width:.3f}" height="{page_height:.3f}" fill="white" />\n'
        f"{body}\n"
        f"</svg>\n"
    )
