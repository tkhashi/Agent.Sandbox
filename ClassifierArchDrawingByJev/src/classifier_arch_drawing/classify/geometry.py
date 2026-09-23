"""通り芯・寸法線の両分類で共有する幾何ヘルパー。"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal

from ..schema import VectorRecord

_CIRCLE_MIN_POINTS = 8
_CIRCLE_RELATIVE_STD_MAX = 0.05

_AXIS_ALIGN_TOLERANCE = 0.5  # ptの高さ/幅がこれ以下なら軸並行とみなす
_COORDINATE_TOLERANCE = 0.75  # pt。共線とみなす座標差の許容値
_MIN_SEGMENT_LENGTH = 0.3  # pt。ほぼ点のような退化した線分は「直線」とみなさない
# (一点鎖線の最短要素である「点」は実測で0.8pt前後あるため、それより明らかに
# 短い断片は誤って紛れ込んだ無関係な線分の残骸とみなして除外する)
MIN_SPAN_RATIO = 0.1  # ページ寸法に対する最低スパン比
_MIN_RHYTHM_MEMBERS = 4  # 「長短」ペア2回分。これ未満は交互パターンとして認めない
_GAP_TO_LONG_RATIO = 0.5  # 直前要素との間隔が「長い」要素の長さの何倍まで許容するか


def record_center(record: VectorRecord) -> tuple[float, float] | None:
    x0, x1 = record.get("x0"), record.get("x1")
    top, bottom = record.get("top"), record.get("bottom")
    if None in (x0, x1, top, bottom):
        return None
    return ((x0 + x1) / 2, (top + bottom) / 2)


def find_circles(records: list[VectorRecord]) -> list[tuple[int, tuple[float, float], float]]:
    """点列の重心距離のばらつきが小さい閉じたcurveを「円」として抽出する。

    半径の絶対値は問わない(通り芯番号の大きい円・寸法線端部の小さい円の
    両方を同じ基準で検出し、呼び出し側が用途に応じてさらに絞り込む)。
    """
    circles: list[tuple[int, tuple[float, float], float]] = []
    for index, record in enumerate(records):
        if record["object_type"] != "curve":
            continue
        pts = record.get("pts")
        if not pts or len(pts) < _CIRCLE_MIN_POINTS:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        distances = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in pts]
        mean_d = sum(distances) / len(distances)
        if mean_d <= 0:
            continue
        std_d = statistics.pstdev(distances)
        if std_d / mean_d <= _CIRCLE_RELATIVE_STD_MAX:
            circles.append((index, (cx, cy), mean_d))
    return circles


@dataclass(frozen=True)
class ChainRun:
    """一点鎖線の「本物の交互リズム(長い線・短い線が交互に続く)」を持つ一連の線分。"""

    indices: tuple[int, ...]
    lo: float
    hi: float
    linewidth: float | None


_SHORT_LONG_RATIO_THRESHOLD = 2.5  # この倍率を超えて急に長くなる箇所を「ドット→ダッシュ」の境界とみなす


def _split_long_short(lengths: list[float]) -> float | None:
    """長さの列を「長い/短い」2群に分ける閾値を、絶対値を決め打ちせずに求める。

    一点鎖線の「点」は常にほぼ一定の短さで揃うのに対し、「線」は交差箇所での
    分断等により長さにばらつきが出ることがある(実測: 点は0.8〜1pt前後で
    安定、線は5〜52ptまで幅広く分布する)。そのため単純に「隣接差が最大の
    箇所」で二分すると、線同士の中でのばらつきの方が点と線の間の差より
    大きく見え、誤った境界を選んでしまう。

    そこで、最も短い方から順に見ていき、**直前の値に対する倍率**が
    最初に大きく跳ね上がった箇所(点の集団を抜けて線の集団に入る境目)を
    境界とする。このPDF固有の絶対値には依存しない。
    """
    uniq_sorted = sorted(set(lengths))
    if len(uniq_sorted) < 2:
        return None
    for prev, cur in zip(uniq_sorted, uniq_sorted[1:]):
        if prev > 0 and cur / prev > _SHORT_LONG_RATIO_THRESHOLD:
            return (prev + cur) / 2
    return None


def find_chain_runs(
    records: list[VectorRecord], coordinate: float, axis: Literal["horizontal", "vertical"]
) -> list[ChainRun]:
    """指定座標を共有する軸並行の`line`から、一点鎖線としての本物の交互リズム
    (長い線の次に短い線、その後また長い線……という繰り返し)を持つランだけを
    抽出する。

    単なる「同じ座標に乗っている線の集合」ではなく、実際に長短が交互に、
    かつ間隔が一定して現れているかを検証する。これにより、たまたま同じ
    座標・線幅の単発の直線(壁の引き通し線等)や、交互しない点線(ドア記号等)
    を「一点鎖線」として誤って採用することを防ぐ。

    同一直線上に無関係な線が挟まっている場合、その地点でリズムが崩れるため
    ランが分断される。挟まれた区間の先で純粋な一点鎖線が続いていれば、
    それは別のランとして独立に検出される。
    """
    candidates: list[tuple[int, float, float, float | None]] = []

    for index, record in enumerate(records):
        if record["object_type"] != "line":
            continue
        x0, x1 = record.get("x0"), record.get("x1")
        top, bottom = record.get("top"), record.get("bottom")
        if None in (x0, x1, top, bottom):
            continue

        if axis == "horizontal":
            if abs(top - bottom) > _AXIS_ALIGN_TOLERANCE:
                continue
            line_coord = (top + bottom) / 2
            span_lo, span_hi = min(x0, x1), max(x0, x1)
        else:
            if abs(x0 - x1) > _AXIS_ALIGN_TOLERANCE:
                continue
            line_coord = (x0 + x1) / 2
            span_lo, span_hi = min(top, bottom), max(top, bottom)

        if span_hi - span_lo < _MIN_SEGMENT_LENGTH:
            continue
        if abs(line_coord - coordinate) > _COORDINATE_TOLERANCE:
            continue

        candidates.append((index, span_lo, span_hi, record.get("linewidth")))

    if not candidates:
        return []

    # 線幅ごとにグループ化する(異なる線幅は物理的に別の線であり、
    # 同じ一点鎖線の一部にはなり得ない)
    by_linewidth: dict[float | None, list[tuple[int, float, float]]] = {}
    for index, lo, hi, linewidth in candidates:
        by_linewidth.setdefault(linewidth, []).append((index, lo, hi))

    runs: list[ChainRun] = []
    for linewidth, items in by_linewidth.items():
        items.sort(key=lambda item: item[1])
        lengths = [hi - lo for _, lo, hi in items]
        boundary = _split_long_short(lengths)
        if boundary is None:
            continue
        labels = ["long" if length >= boundary else "short" for length in lengths]

        run_members: list[int] = [0]
        for i in range(1, len(items)):
            _prev_index, _prev_lo, prev_hi = items[i - 1]
            _cur_index, cur_lo, _cur_hi = items[i]
            gap = cur_lo - prev_hi
            alternates = labels[i] != labels[i - 1]
            # このペアの「長い」方の要素の長さを基準にギャップの妥当性を判定する
            long_length = lengths[i] if labels[i] == "long" else lengths[i - 1]
            gap_ok = 0 <= gap <= long_length * _GAP_TO_LONG_RATIO
            if alternates and gap_ok:
                run_members.append(i)
            else:
                runs.extend(_finalize_runs(run_members, items, labels, linewidth))
                run_members = [i]
        runs.extend(_finalize_runs(run_members, items, labels, linewidth))

    return runs


def _finalize_runs(
    member_positions: list[int],
    items: list[tuple[int, float, float]],
    labels: list[str],
    linewidth: float | None,
) -> list[ChainRun]:
    if len(member_positions) < _MIN_RHYTHM_MEMBERS:
        return []
    member_labels = {labels[i] for i in member_positions}
    if member_labels != {"long", "short"}:
        return []
    indices = tuple(items[i][0] for i in member_positions)
    lo = min(items[i][1] for i in member_positions)
    hi = max(items[i][2] for i in member_positions)
    return [ChainRun(indices=indices, lo=lo, hi=hi, linewidth=linewidth)]


_TOUCH_POINT_TOLERANCE = 1.0  # pt。ある点が線分の端点に接しているとみなす許容値
_LINEWIDTH_TOLERANCE = 0.01  # pt。線幅が「同一」とみなす許容差


def find_touching_lines(
    records: list[VectorRecord], point: tuple[float, float], linewidth: float | None, exclude: set[int]
) -> tuple[int, ...]:
    """指定した点を直接の端点として持ち、かつ指定した線幅と一致する`line`を集める。

    寸法線の端部から直接交差してちょっとだけ伸びている線(その場限りの短い
    ティックマーク)は、一点鎖線のような規模の大きい共線クラスタを作らない
    ため`find_collinear_cluster`だけでは拾えない。そうした線は必ず寸法線
    本体(端部同士をつなぐ線)と同一線幅で、かつ端部の点に直接接しているため、
    この2条件で判定する。
    """
    px, py = point
    found: list[int] = []
    for index, record in enumerate(records):
        if index in exclude or record["object_type"] != "line":
            continue
        if linewidth is not None:
            record_lw = record.get("linewidth")
            if record_lw is None or abs(record_lw - linewidth) > _LINEWIDTH_TOLERANCE:
                continue
        x0, x1, top, bottom = (
            record.get("x0"),
            record.get("x1"),
            record.get("top"),
            record.get("bottom"),
        )
        if None in (x0, x1, top, bottom):
            continue
        for ex, ey in ((x0, top), (x1, bottom)):
            if ((ex - px) ** 2 + (ey - py) ** 2) ** 0.5 <= _TOUCH_POINT_TOLERANCE:
                found.append(index)
                break
    return tuple(found)


def has_significant_span(run: ChainRun, page_dimension: float | None) -> bool:
    """一点鎖線のランが、通り芯のようにページ寸法に対して十分長いかを判定する。

    交互リズム自体は`find_chain_runs`で既に検証済みのため、ここでは
    「装飾記号内で偶然2〜3回だけ交互した短いパターン」のような、規模の
    小さい誤検出をさらに除外するためのスパン判定のみを行う。
    """
    if page_dimension is not None and (run.hi - run.lo) < page_dimension * MIN_SPAN_RATIO:
        return False
    return True
