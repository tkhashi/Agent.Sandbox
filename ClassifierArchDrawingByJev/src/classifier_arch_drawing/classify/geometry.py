"""通り芯・寸法線・壁の分類で共有する幾何ヘルパー。"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Literal

from ..schema import VectorRecord

_CIRCLE_MIN_POINTS = 8
_CIRCLE_RELATIVE_STD_MAX = 0.05
_ARC_CLOSURE_RATIO_MAX = 0.3  # 半径に対する始点-終点間距離の許容比率。これ以下なら閉ループ(円)とみなす

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


def _fit_circle(pts: list[tuple[float, float]] | None) -> tuple[tuple[float, float], float] | None:
    """点列の重心・平均半径を求め、円(の一部)とみなせるほど分散が小さいか判定する。

    閉ループかどうかは判定しない(呼び出し側が`_is_closed`で別途判定する)。
    """
    if not pts or len(pts) < _CIRCLE_MIN_POINTS:
        return None
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    distances = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in pts]
    mean_d = sum(distances) / len(distances)
    if mean_d <= 0:
        return None
    std_d = statistics.pstdev(distances)
    if std_d / mean_d > _CIRCLE_RELATIVE_STD_MAX:
        return None
    return (cx, cy), mean_d


def _is_closed(pts: list[tuple[float, float]], radius: float) -> bool:
    if radius <= 0:
        return False
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    gap = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return gap <= radius * _ARC_CLOSURE_RATIO_MAX


def find_circles(records: list[VectorRecord]) -> list[tuple[int, tuple[float, float], float]]:
    """点列の重心距離のばらつきが小さい、始点と終点がほぼ一致する閉じたcurveを
    「円」として抽出する。

    半径の絶対値は問わない(通り芯番号の大きい円・寸法線端部の小さい円の
    両方を同じ基準で検出し、呼び出し側が用途に応じてさらに絞り込む)。
    """
    circles: list[tuple[int, tuple[float, float], float]] = []
    for index, record in enumerate(records):
        if record["object_type"] != "curve":
            continue
        pts = record.get("pts")
        fit = _fit_circle(pts)
        if fit is None:
            continue
        center, radius = fit
        if not _is_closed(pts, radius):
            continue
        circles.append((index, center, radius))
    return circles


def _fit_arc_circle(pts: list[tuple[float, float]] | None) -> tuple[tuple[float, float], float] | None:
    """点列に円を最小二乗フィット(Kasa法)し、中心・半径を求める。

    `_fit_circle`(点列の重心を中心とみなす簡易法)は、閉じた円では
    重心がほぼ中心と一致するため機能するが、**開いた円弧**(扇形の一部等)
    では点群の重心が円の中心から大きくずれるため使えない。そのため
    円弧の検出には、円の方程式`x^2+y^2 = 2*cx*x + 2*cy*y + c`への
    線形最小二乗フィットを別途行う。
    """
    if not pts or len(pts) < _CIRCLE_MIN_POINTS:
        return None
    n = len(pts)
    sx = sy = sxx = syy = sxy = sxxx = syyy = sxyy = sxxy = 0.0
    for x, y in pts:
        sx += x
        sy += y
        sxx += x * x
        syy += y * y
        sxy += x * y
        sxxx += x**3
        syyy += y**3
        sxyy += x * y * y
        sxxy += x * x * y

    a11, a12, a13 = 2 * sxx, 2 * sxy, sx
    a21, a22, a23 = 2 * sxy, 2 * syy, sy
    a31, a32, a33 = 2 * sx, 2 * sy, float(n)
    b1, b2, b3 = sxxx + sxyy, sxxy + syyy, sxx + syy

    def det3(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    det = det3([[a11, a12, a13], [a21, a22, a23], [a31, a32, a33]])
    if abs(det) < 1e-9:
        return None
    cx = det3([[b1, a12, a13], [b2, a22, a23], [b3, a32, a33]]) / det
    cy = det3([[a11, b1, a13], [a21, b2, a23], [a31, b3, a33]]) / det
    c = det3([[a11, a12, b1], [a21, a22, b2], [a31, a32, b3]]) / det
    r2 = c + cx * cx + cy * cy
    if r2 <= 0:
        return None
    radius = r2**0.5

    distances = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in pts]
    mean_d = sum(distances) / len(distances)
    if mean_d <= 0:
        return None
    std_d = statistics.pstdev(distances)
    if std_d / mean_d > _CIRCLE_RELATIVE_STD_MAX:
        return None
    return (cx, cy), radius


def find_arcs(
    records: list[VectorRecord],
) -> list[tuple[int, tuple[float, float], float, float, float]]:
    """点列が円の一部に沿っているが、始点と終点が一致しない(閉じていない)
    curveを「円弧」として抽出する(ドア記号の扇形等)。

    戻り値は`(index, center, radius, start_angle, end_angle)`のリスト。
    角度はラジアンで、`atan2(y - center_y, x - center_x)`を基準とする。
    """
    arcs: list[tuple[int, tuple[float, float], float, float, float]] = []
    for index, record in enumerate(records):
        if record["object_type"] != "curve":
            continue
        pts = record.get("pts")
        if not pts:
            continue
        fit = _fit_arc_circle(pts)
        if fit is None:
            continue
        center, radius = fit
        if _is_closed(pts, radius):
            continue
        cx, cy = center
        x0, y0 = pts[0]
        x1, y1 = pts[-1]
        start_angle = math.atan2(y0 - cy, x0 - cx)
        end_angle = math.atan2(y1 - cy, x1 - cx)
        arcs.append((index, center, radius, start_angle, end_angle))
    return arcs


def _char_orientation(
    record: VectorRecord,
) -> tuple[Literal["horizontal", "vertical"], float, float, float] | None:
    """charの`matrix`から(読み方向の軸, フォントサイズ, 読み方向の単位ベクトル)を求める。"""
    matrix = record.get("matrix")
    if not matrix:
        return None
    a, b, _c, _d, _e, _f = matrix
    font_size = math.hypot(a, b)
    if font_size <= 0:
        return None
    angle = math.degrees(math.atan2(b, a)) % 180
    axis: Literal["horizontal", "vertical"] = "horizontal" if (angle < 45 or angle > 135) else "vertical"
    unit_dx, unit_dy = a / font_size, -b / font_size
    return axis, font_size, unit_dx, unit_dy


def collect_char_runs(
    records: list[VectorRecord],
    accept_char: Callable[[str], bool],
    perp_tolerance: float = _COORDINATE_TOLERANCE,
    gap_factor: float = 0.6,
) -> list[list[dict]]:
    """`accept_char`を満たすcharを、`matrix`が示す読み方向に沿って隣接クラスタリングする。

    数値ワード(「3,700」等)復元にも部屋名文字列(「トイレ」等)復元にも使える
    共通ロジック。最終的な文字列の妥当性判定(正規表現・キーワード一致等)は
    呼び出し側が各runに対して行う(このヘルパーはクラスタリングのみを担う)。

    戻り値の各runは、読み方向に並んだcharの候補dictのリスト:
    `{"index","axis","font_size","perp","reading_key","text","x0","x1","top","bottom"}`
    """
    candidates = []
    for index, record in enumerate(records):
        if record["object_type"] != "char":
            continue
        text = record.get("text") or ""
        if not accept_char(text):
            continue
        orientation = _char_orientation(record)
        if orientation is None:
            continue
        axis, font_size, unit_dx, unit_dy = orientation
        x0, x1, top, bottom = (
            record.get("x0"),
            record.get("x1"),
            record.get("top"),
            record.get("bottom"),
        )
        if None in (x0, x1, top, bottom):
            continue
        perp = (top + bottom) / 2 if axis == "horizontal" else (x0 + x1) / 2
        reading_key = x0 * unit_dx + top * unit_dy
        candidates.append(
            {
                "index": index,
                "axis": axis,
                "font_size": font_size,
                "perp": perp,
                "reading_key": reading_key,
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
            }
        )

    runs: list[list[dict]] = []
    for axis in ("horizontal", "vertical"):
        items = sorted((c for c in candidates if c["axis"] == axis), key=lambda c: c["perp"])

        perp_clusters: list[list[dict]] = []
        for item in items:
            if perp_clusters and abs(item["perp"] - perp_clusters[-1][-1]["perp"]) <= perp_tolerance:
                perp_clusters[-1].append(item)
            else:
                perp_clusters.append([item])

        for cluster in perp_clusters:
            cluster.sort(key=lambda c: c["reading_key"])
            run: list[dict] = [cluster[0]]
            for prev, cur in zip(cluster, cluster[1:]):
                gap = cur["reading_key"] - prev["reading_key"]
                threshold = max(prev["font_size"], cur["font_size"]) * gap_factor
                if gap > threshold:
                    runs.append(run)
                    run = [cur]
                else:
                    run.append(cur)
            runs.append(run)

    return runs


def collect_axis_aligned_lines(records: list[VectorRecord]) -> dict[str, list[dict]]:
    """`line`を水平/垂直の軸並行なものに絞り込み、座標・区間・線幅を抽出する。

    寸法線分類(隙間なく接続する線のネットワーク化)・壁分類(平行な2本線の
    ペア探索)の両方が、まず軸並行線をこの形式で集めてから独自の後続処理を
    行う、という共通の前処理として使う。
    """
    out: dict[str, list[dict]] = {"horizontal": [], "vertical": []}
    for index, record in enumerate(records):
        if record["object_type"] != "line":
            continue
        x0, x1, top, bottom = (
            record.get("x0"),
            record.get("x1"),
            record.get("top"),
            record.get("bottom"),
        )
        if None in (x0, x1, top, bottom):
            continue
        linewidth = record.get("linewidth")
        if abs(top - bottom) <= _AXIS_ALIGN_TOLERANCE:
            out["horizontal"].append(
                {
                    "index": index,
                    "coord": (top + bottom) / 2,
                    "lo": min(x0, x1),
                    "hi": max(x0, x1),
                    "linewidth": linewidth,
                }
            )
        elif abs(x0 - x1) <= _AXIS_ALIGN_TOLERANCE:
            out["vertical"].append(
                {
                    "index": index,
                    "coord": (x0 + x1) / 2,
                    "lo": min(top, bottom),
                    "hi": max(top, bottom),
                    "linewidth": linewidth,
                }
            )
    return out


@dataclass(frozen=True)
class ChainRun:
    """一点鎖線の「本物の交互リズム(長い線・短い線が交互に続く)」を持つ一連の線分。"""

    indices: tuple[int, ...]
    lo: float
    hi: float
    linewidth: float | None


_SHORT_LONG_RATIO_THRESHOLD = 2.5  # この倍率を超えて急に長くなる箇所を「ドット→ダッシュ」の境界とみなす


def split_by_relative_jump(values: list[float], ratio_threshold: float) -> float | None:
    """値の列を2群に分ける閾値を、絶対値を決め打ちせずに求める。

    最も小さい方から順に見ていき、**直前の値に対する倍率**が最初に
    `ratio_threshold`を超えて跳ね上がった箇所を境界とする。この境界より
    小さい値の集団と大きい値の集団を分けたい場合に使う汎用ヘルパー
    (一点鎖線の「点/線」の長さ分割、壁厚として妥当な間隔の上限導出、
    壁スコアの合否閾値導出など、複数の分類タスクで共通して使う)。

    2群を分ける自然な境目が無い(倍率ジャンプが一度も閾値を超えない)場合は
    `None`を返す。
    """
    uniq_sorted = sorted(set(values))
    if len(uniq_sorted) < 2:
        return None
    for prev, cur in zip(uniq_sorted, uniq_sorted[1:]):
        if prev > 0 and cur / prev > ratio_threshold:
            return (prev + cur) / 2
    return None


def _split_long_short(lengths: list[float]) -> float | None:
    """長さの列を「長い/短い」2群に分ける閾値を求める(`split_by_relative_jump`の特化版)。

    一点鎖線の「点」は常にほぼ一定の短さで揃うのに対し、「線」は交差箇所での
    分断等により長さにばらつきが出ることがある(実測: 点は0.8〜1pt前後で
    安定、線は5〜52ptまで幅広く分布する)。そのため単純に「隣接差が最大の
    箇所」で二分すると、線同士の中でのばらつきの方が点と線の間の差より
    大きく見え、誤った境界を選んでしまう。

    そこで、最も短い方から順に見ていき、**直前の値に対する倍率**が
    最初に大きく跳ね上がった箇所(点の集団を抜けて線の集団に入る境目)を
    境界とする。このPDF固有の絶対値には依存しない。
    """
    return split_by_relative_jump(lengths, _SHORT_LONG_RATIO_THRESHOLD)


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
