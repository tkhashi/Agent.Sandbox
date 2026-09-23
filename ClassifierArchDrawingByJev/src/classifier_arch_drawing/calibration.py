"""線幅(linewidth)の自動キャリブレーション。

pdfplumberが報告する`linewidth`は、PDFの作成ソフトによっては
実際にPDFビューアで描画される太さと一致しないことがある
(例: CADのペン幅テーブルがそのままpt値として乗ってくる等)。

page.to_image()(pypdfium2による実際の描画結果)を正解とみなし、
孤立した直線サンプルの実測太さと報告linewidthの比を取ることで、
「報告値→実際の描画太さ」への補正係数をPDFごとに自動検出する。
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

import pdfplumber
from PIL import Image

from .schema import VectorRecord

_BACKGROUND_THRESHOLD = 220  # グレースケール明度がこれ以上なら背景(白)とみなす
_RATIO_MIN = 0.01
_RATIO_MAX = 2.0
_MAD_Z_THRESHOLD = 3.5
_AXIS_ALIGN_TOLERANCE_DEG = 1.0


@dataclass(frozen=True)
class CalibrationResult:
    scale: float
    sample_count: int
    candidate_count: int
    per_sample_ratios: list[float] = field(default_factory=list)
    method: Literal[
        "measured", "fallback_no_samples", "fallback_disabled", "manual"
    ] = "fallback_no_samples"
    confidence: float = 0.0


@dataclass(frozen=True)
class _Segment:
    x0: float
    y0: float
    x1: float
    y1: float
    linewidth: float

    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    def is_axis_aligned(self, tol_deg: float) -> bool:
        angle = math.degrees(math.atan2(abs(self.y1 - self.y0), abs(self.x1 - self.x0)))
        return angle <= tol_deg or angle >= 90 - tol_deg

    def is_horizontal(self) -> bool:
        return abs(self.y1 - self.y0) <= abs(self.x1 - self.x0)

    def midpoint(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def bbox(self, margin: float) -> tuple[float, float, float, float]:
        return (
            min(self.x0, self.x1) - margin,
            min(self.y0, self.y1) - margin,
            max(self.x0, self.x1) + margin,
            max(self.y0, self.y1) + margin,
        )


def _record_bbox(record: VectorRecord) -> tuple[float, float, float, float] | None:
    x0, x1 = record.get("x0"), record.get("x1")
    top, bottom = record.get("top"), record.get("bottom")
    if None in (x0, x1, top, bottom):
        return None
    return (min(x0, x1), min(top, bottom), max(x0, x1), max(top, bottom))


def _bbox_overlaps(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _record_to_segments(record: VectorRecord) -> list[_Segment]:
    linewidth = record.get("linewidth")
    if linewidth is None or linewidth <= 0:
        return []

    if record["object_type"] == "line":
        x0, x1 = record.get("x0"), record.get("x1")
        top, bottom = record.get("top"), record.get("bottom")
        if None in (x0, x1, top, bottom):
            return []
        return [_Segment(x0, top, x1, bottom, linewidth)]

    if record["object_type"] == "rect":
        bbox = _record_bbox(record)
        if bbox is None:
            return []
        x0, y0, x1, y1 = bbox
        return [
            _Segment(x0, y0, x1, y0, linewidth),
            _Segment(x0, y1, x1, y1, linewidth),
            _Segment(x0, y0, x0, y1, linewidth),
            _Segment(x1, y0, x1, y1, linewidth),
        ]

    return []


def _min_segment_length(page_width: float, page_height: float) -> float:
    page_diag = math.hypot(page_width, page_height)
    return max(page_diag * 0.005, 5.0)


def _min_required_samples(distinct_linewidth_count: int) -> int:
    return max(min(distinct_linewidth_count, 3) * 2, 3)


def _select_isolated_straight_samples(
    records: list[VectorRecord],
    page_width: float,
    page_height: float,
    max_samples: int,
) -> list[_Segment]:
    min_length = _min_segment_length(page_width, page_height)

    all_bboxes = [b for r in records if (b := _record_bbox(r)) is not None]

    candidates: list[_Segment] = []
    for record in records:
        if record["object_type"] not in ("line", "rect"):
            continue
        for segment in _record_to_segments(record):
            if not segment.is_axis_aligned(_AXIS_ALIGN_TOLERANCE_DEG):
                continue
            if segment.length() < min_length:
                continue
            margin = max(segment.linewidth, 1.0)
            neighborhood = segment.bbox(margin)
            self_bbox = segment.bbox(0.0)
            conflicts = sum(
                1
                for other_bbox in all_bboxes
                if other_bbox != self_bbox and _bbox_overlaps(neighborhood, other_bbox)
            )
            if conflicts == 0:
                candidates.append(segment)

    grouped: dict[float, list[_Segment]] = defaultdict(list)
    for segment in candidates:
        grouped[round(segment.linewidth, 3)].append(segment)

    if not grouped:
        return []

    samples: list[_Segment] = []
    groups = list(grouped.values())
    idx = 0
    while len(samples) < max_samples and any(groups):
        group = groups[idx % len(groups)]
        if group:
            samples.append(group.pop())
        idx += 1
        if idx > max_samples * len(groups) + len(groups):
            break

    return samples


def _is_dark(pixel: int) -> bool:
    return pixel < _BACKGROUND_THRESHOLD


def _count_dark_run(
    gray: Image.Image, fixed_px: int, center_px: int, axis: Literal["x", "y"], max_scan_px: int
) -> int | None:
    width, height = gray.size
    load = gray.load()

    def pixel_at(pos: int) -> int | None:
        if axis == "y":
            x, y = fixed_px, pos
        else:
            x, y = pos, fixed_px
        if not (0 <= x < width and 0 <= y < height):
            return None
        return load[x, y]

    center = center_px
    if not _is_dark(pixel_at(center) or 255):
        found = None
        for offset in range(1, 4):
            for cand in (center - offset, center + offset):
                px = pixel_at(cand)
                if px is not None and _is_dark(px):
                    found = cand
                    break
            if found is not None:
                break
        if found is None:
            return None
        center = found

    lo = hi = center
    while (hi - center) < max_scan_px:
        px = pixel_at(hi + 1)
        if px is None or not _is_dark(px):
            break
        hi += 1
    while (center - lo) < max_scan_px:
        px = pixel_at(lo - 1)
        if px is None or not _is_dark(px):
            break
        lo -= 1

    run_length = hi - lo + 1
    if run_length <= 0 or run_length >= max_scan_px:
        return None
    return run_length


def _measure_stroke_width_px(gray: Image.Image, segment: _Segment, px_per_pt: float) -> float | None:
    mid_x_pt, mid_y_pt = segment.midpoint()
    mid_x_px = round(mid_x_pt * px_per_pt)
    mid_y_px = round(mid_y_pt * px_per_pt)
    max_scan_px = int(20 * px_per_pt)

    if segment.is_horizontal():
        return _count_dark_run(gray, mid_x_px, mid_y_px, "y", max_scan_px)
    return _count_dark_run(gray, mid_y_px, mid_x_px, "x", max_scan_px)


def _robust_scale_from_ratios(ratios: list[float]) -> tuple[float, float]:
    median = statistics.median(ratios)
    mad = statistics.median([abs(r - median) for r in ratios]) or 1e-6

    filtered = [r for r in ratios if abs(r - median) / (1.4826 * mad) <= _MAD_Z_THRESHOLD]
    if not filtered:
        filtered = ratios

    scale = statistics.median(filtered)
    stdev = statistics.pstdev(filtered) if len(filtered) > 1 else 0.0
    cv = stdev / scale if scale > 0 else 1.0
    sample_factor = min(len(filtered) / 20, 1.0)
    consistency_factor = max(0.0, 1.0 - cv)
    confidence = round(sample_factor * consistency_factor, 3)

    return scale, confidence


def calibrate_linewidth(
    page: pdfplumber.page.Page,
    records: list[VectorRecord],
    *,
    resolution: int = 200,
    max_samples: int = 60,
) -> CalibrationResult:
    distinct_linewidths = {
        round(r["linewidth"], 3)
        for r in records
        if r["object_type"] in ("line", "rect") and r.get("linewidth")
    }
    min_required = _min_required_samples(len(distinct_linewidths))

    candidates = _select_isolated_straight_samples(
        records, page.width, page.height, max_samples
    )
    if len(candidates) < min_required:
        return CalibrationResult(
            scale=1.0,
            sample_count=0,
            candidate_count=len(candidates),
            method="fallback_no_samples",
        )

    raster = page.to_image(resolution=resolution).original.convert("L")
    px_per_pt = resolution / 72.0

    ratios: list[float] = []
    used_linewidths: set[float] = set()
    for segment in candidates:
        measured_px = _measure_stroke_width_px(raster, segment, px_per_pt)
        if measured_px is None:
            continue
        measured_pt = measured_px / px_per_pt
        ratio = measured_pt / segment.linewidth
        if _RATIO_MIN <= ratio <= _RATIO_MAX:
            ratios.append(ratio)
            used_linewidths.add(round(segment.linewidth, 3))

    if len(ratios) < min_required:
        return CalibrationResult(
            scale=1.0,
            sample_count=len(ratios),
            candidate_count=len(candidates),
            per_sample_ratios=ratios,
            method="fallback_no_samples",
        )

    scale, confidence = _robust_scale_from_ratios(ratios)
    if len(used_linewidths) <= 1:
        confidence = round(confidence * 0.5, 3)

    return CalibrationResult(
        scale=scale,
        sample_count=len(ratios),
        candidate_count=len(candidates),
        per_sample_ratios=ratios,
        method="measured",
        confidence=confidence,
    )
