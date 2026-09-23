"""寸法線(線)・寸法(数値)の分類。

判定方針(詳細はdocs/adr/0007-dimension-line-classification.mdを参照):

1. 数字/カンマの`char`を、文字の`matrix`(回転行列)が示す読み方向で
   隣接クラスタリングし、「3,700」のような数値ワードに復元する
2. `line`を軸並行(水平/垂直)に絞り込み、**端点同士が接続していて
   (隙間がない)、かつ線幅が同一である**もの同士を連結成分(ネットワーク)
   としてグルーピングする。通り芯の一点鎖線は隙間だらけのため、この時点で
   自然に除外される
3. 各数値ワードについて、その読み方向に応じた直下/直脇にあり、かつ
   **数値ワードの中心が区間のほぼ中央に来る**区間を探して対応付ける
   (寸法値は寸法線のほぼ中央に記載されるという慣行を利用する)
4. 各区間の両端点について、その点を中心とする小さい円(端部マーカー)を
   探索して記録する
5. 各区間の両端点について、伸びる線を(a)(b)の2種類を合わせて探索して記録する
   - (a) 端部の点に直接交差し、寸法線本体と同一線幅を持つ線
     (「端部と直接交差してからちょっとだけ伸びている線」)
   - (b) 端部と同一座標(x/y)を共有し、一点鎖線として妥当な規模(本数・スパン)
     を持つ線分群(通り芯上と同じ判定基準。単純な座標一致だけでは無関係な
     線を拾ってしまうため、多数決で線幅が異なるものは除外する)
   直線(一点鎖線でも(a)でもない単発の長い実線)はどちらにも該当しないため
   除外される
6. 区間の両端点の座標が、既存の通り芯分類(`GridLine`)の座標と一致するかで
   「通り芯由来」か「独立」かを判定する
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from ..schema import VectorRecord
from .geometry import (
    MIN_SPAN_RATIO,
    collect_axis_aligned_lines,
    collect_char_runs,
    find_chain_runs,
    find_circles,
    find_touching_lines,
)
from .grid import GridLine

_NUMBER_CHAR_PATTERN = re.compile(r"^[0-9,]$")
_NUMBER_WORD_PATTERN = re.compile(r"^[0-9]+(,[0-9]+)*$")

_AXIS_ALIGN_TOLERANCE = 0.5  # pt
_COORDINATE_TOLERANCE = 0.75  # pt。同一座標とみなす許容値
_TOUCH_TOLERANCE = 0.75  # pt。線分同士が接続しているとみなす許容値
_WORD_CHAR_GAP_FACTOR = 0.6  # フォントサイズに対する文字間ギャップの許容倍率
_WORD_TO_LINE_GAP_FACTOR = 1.5  # フォントサイズに対する数値-線間ギャップの許容倍率
_MARKER_SEARCH_TOLERANCE = 2.0  # pt。端部座標から端部マーカー円を探す許容範囲
_CENTERING_TOLERANCE_RATIO = 0.25  # 区間長に対する、数値ワード中心のズレ許容比率
_LINEWIDTH_TOLERANCE = 0.01  # pt。線幅が「同一」とみなす許容差


@dataclass(frozen=True)
class DimensionSegment:
    value_text: str
    value_char_indices: tuple[int, ...]
    axis: Literal["horizontal", "vertical"]
    coordinate: float
    line_indices: tuple[int, ...]
    start_point: tuple[float, float]
    end_point: tuple[float, float]
    start_marker_index: int | None
    end_marker_index: int | None
    start_extension_indices: tuple[int, ...]
    end_extension_indices: tuple[int, ...]
    is_axis_derived: bool


def _collect_numeric_words(records: list[VectorRecord]) -> list[dict]:
    runs = collect_char_runs(
        records,
        lambda text: bool(_NUMBER_CHAR_PATTERN.match(text)),
        perp_tolerance=_COORDINATE_TOLERANCE,
        gap_factor=_WORD_CHAR_GAP_FACTOR,
    )
    words = [_finalize_word(run, run[0]["axis"]) for run in runs]
    return [w for w in words if w is not None]


def _finalize_word(run: list[dict], axis: Literal["horizontal", "vertical"]) -> dict | None:
    text = "".join(item["text"] for item in run)
    if not _NUMBER_WORD_PATTERN.match(text):
        return None
    if len(text.replace(",", "")) < 2:
        # 単独の1桁だけの検出は、寸法値ではなく他の注記文字(記号番号等)の
        # 誤検出であることが多いため除外する(本図面の実寸法値は最小3桁)。
        return None
    xs = [item["x0"] for item in run] + [item["x1"] for item in run]
    tops = [item["top"] for item in run] + [item["bottom"] for item in run]
    font_size = sum(item["font_size"] for item in run) / len(run)
    return {
        "text": text,
        "char_indices": tuple(item["index"] for item in run),
        "axis": axis,
        "font_size": font_size,
        "along_lo": min(xs) if axis == "horizontal" else min(tops),
        "along_hi": max(xs) if axis == "horizontal" else max(tops),
        "perp": sum(item["perp"] for item in run) / len(run),
    }


def _build_networks(items: list[dict]) -> list[dict]:
    """同じ座標・同じ線幅を共有し、端点同士が接続している線分同士をネットワークにまとめる。

    寸法線(端部同士をつなぐ線)は必ず単一の線幅で描かれるため、たとえ座標が
    一致し端点が接続していても、線幅が異なる線は同じ寸法線とはみなさず
    ネットワークを分割する。
    """
    items = sorted(items, key=lambda i: i["coord"])
    coord_clusters: list[list[dict]] = []
    for item in items:
        if coord_clusters and abs(item["coord"] - coord_clusters[-1][-1]["coord"]) <= _COORDINATE_TOLERANCE:
            coord_clusters[-1].append(item)
        else:
            coord_clusters.append([item])

    networks: list[dict] = []
    for cluster in coord_clusters:
        coordinate = sum(i["coord"] for i in cluster) / len(cluster)
        # 同一範囲(lo,hi)の重複オブジェクトはまとめる
        by_range: dict[tuple[float, float], list[dict]] = {}
        for item in cluster:
            key = (round(item["lo"], 2), round(item["hi"], 2))
            by_range.setdefault(key, []).append(item)
        ranged = sorted(
            (
                {
                    "lo": lo,
                    "hi": hi,
                    "indices": [i["index"] for i in items_],
                    "linewidth": items_[0]["linewidth"],
                }
                for (lo, hi), items_ in by_range.items()
            ),
            key=lambda r: r["lo"],
        )

        run: list[dict] = [ranged[0]]
        for prev, cur in zip(ranged, ranged[1:]):
            same_linewidth = (
                run[-1]["linewidth"] is not None
                and cur["linewidth"] is not None
                and abs(run[-1]["linewidth"] - cur["linewidth"]) <= _LINEWIDTH_TOLERANCE
            )
            if cur["lo"] - run[-1]["hi"] <= _TOUCH_TOLERANCE and same_linewidth:
                run.append(cur)
            else:
                networks.append({"coordinate": coordinate, "segments": run})
                run = [cur]
        networks.append({"coordinate": coordinate, "segments": run})

    return networks


def _point_for(axis: Literal["horizontal", "vertical"], coordinate: float, along: float) -> tuple[float, float]:
    return (along, coordinate) if axis == "horizontal" else (coordinate, along)


def _find_marker(records: list[VectorRecord], point: tuple[float, float]) -> int | None:
    px, py = point
    best_index, best_dist = None, _MARKER_SEARCH_TOLERANCE
    for index, center, _radius in find_circles(records):
        dist = math.hypot(center[0] - px, center[1] - py)
        if dist <= best_dist:
            best_index, best_dist = index, dist
    return best_index


def _find_extensions(
    records: list[VectorRecord],
    axis: Literal["horizontal", "vertical"],
    point: tuple[float, float],
    main_linewidth: float | None,
    exclude: set[int],
    page_width: float | None,
    page_height: float | None,
) -> tuple[int, ...]:
    """寸法線の端部から伸びる線を集める。以下の2種類を両方とも対象とする。

    (a) 端部の点に直接交差し、寸法線本体(端部同士をつなぐ線)と同一線幅を
        持つ線。「端部と直接交差してからちょっとだけ伸びている線」は、一点鎖線
        のような規模のクラスタを作らないため(b)だけでは拾えない。逆に、
        線幅が異なる線は寸法線側の要素ではないとみなし、直接触れていても除外する
    (b) 端部と同一座標(x/y)を共有し、**本物の交互リズム(長い線・短い線が
        交互に続く)**を持つ一点鎖線のラン。伸長線(引き通し線)は寸法線側・
        壁側の双方に意図的な隙間を持たせて複数の線分に分けて描かれるため、
        「接続しているか」ではなく「同一座標を共有しているか」で判定する
        (通り芯分類と同じ考え方)。`find_chain_runs`は単なる座標一致・線幅
        一致だけでなく、実際に長短が交互に現れているかまで検証するため、
        単発の直線(ドア/窓記号の細部、壁の引き通し線等)を「一点鎖線」として
        誤って採用しない。同一座標上に無関係な線が挟まっている場合はそこで
        ランが分断されるため、挟まれた区間は採用されず、その先で純粋な
        一点鎖線が続いていれば別のランとして独立に採用される

    原則として直線(一点鎖線としての交互リズムを持たない、単発の長い実線)は
    寸法線とはみなさない。(a)の「端部に直接交差する」という例外だけがこの
    原則を上書きする。
    """
    # 寸法線自身の軸(水平/垂直)と直交する向きの線が「伸びる線」になる
    perpendicular_axis = "vertical" if axis == "horizontal" else "horizontal"
    coordinate = point[0] if axis == "horizontal" else point[1]
    page_dimension = page_height if perpendicular_axis == "vertical" else page_width

    touching = set(find_touching_lines(records, point, main_linewidth, exclude))

    chain: set[int] = set()
    for run in find_chain_runs(records, coordinate, perpendicular_axis):
        if page_dimension is None or (run.hi - run.lo) >= page_dimension * MIN_SPAN_RATIO:
            chain.update(run.indices)

    return tuple(sorted((touching | chain) - exclude))


def _is_axis_derived(
    axis: Literal["horizontal", "vertical"],
    point: tuple[float, float],
    grid_lines: list[GridLine],
) -> bool:
    check_axis = "vertical" if axis == "horizontal" else "horizontal"
    coordinate = point[0] if axis == "horizontal" else point[1]
    return any(
        g.axis == check_axis and abs(g.coordinate - coordinate) <= _COORDINATE_TOLERANCE
        for g in grid_lines
    )


def classify_dimension_lines(
    records: list[VectorRecord],
    grid_lines: list[GridLine],
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[DimensionSegment]:
    words = _collect_numeric_words(records)
    aligned = collect_axis_aligned_lines(records)

    networks_by_axis = {
        axis: _build_networks(aligned[axis]) for axis in ("horizontal", "vertical")
    }

    results: list[DimensionSegment] = []
    for word in words:
        axis = word["axis"]
        word_center = (word["along_lo"] + word["along_hi"]) / 2
        best_segment = None
        best_center_offset = None

        for network in networks_by_axis[axis]:
            perp_gap = network["coordinate"] - word["perp"]
            if axis == "horizontal":
                # 数値は水平寸法線の直上にあることを想定(線のtopの方が数値より大きい)
                if not (0 <= perp_gap <= word["font_size"] * _WORD_TO_LINE_GAP_FACTOR):
                    continue
            else:
                if abs(perp_gap) > word["font_size"] * _WORD_TO_LINE_GAP_FACTOR:
                    continue

            for segment in network["segments"]:
                segment_length = segment["hi"] - segment["lo"]
                segment_center = (segment["lo"] + segment["hi"]) / 2
                center_offset = abs(word_center - segment_center)
                # 寸法値は寸法線のほぼ中央に記載される、という慣行を主基準にする
                if center_offset > segment_length * _CENTERING_TOLERANCE_RATIO:
                    continue

                if best_center_offset is None or center_offset < best_center_offset:
                    best_center_offset = center_offset
                    best_segment = (network, segment)

        if best_segment is None:
            continue

        network, segment = best_segment
        coordinate = network["coordinate"]
        start_point = _point_for(axis, coordinate, segment["lo"])
        end_point = _point_for(axis, coordinate, segment["hi"])
        line_indices = tuple(segment["indices"])
        exclude = set(line_indices)

        start_marker = _find_marker(records, start_point)
        end_marker = _find_marker(records, end_point)
        if start_marker is None or end_marker is None:
            # 寸法線の端部には必ず丸のマーカーがある。中央性基準だけでは
            # 偶然centeringしてしまう無関係な線(部屋の造作線等)を排除しきれない
            # ため、両端に端部マーカーが実在することを最終確認条件にする。
            continue

        main_linewidths = [records[i].get("linewidth") for i in line_indices]
        main_linewidth = main_linewidths[0] if main_linewidths else None

        start_extensions = _find_extensions(
            records, axis, start_point, main_linewidth, exclude, page_width, page_height
        )
        end_extensions = _find_extensions(
            records, axis, end_point, main_linewidth, exclude, page_width, page_height
        )

        results.append(
            DimensionSegment(
                value_text=word["text"],
                value_char_indices=word["char_indices"],
                axis=axis,
                coordinate=coordinate,
                line_indices=line_indices,
                start_point=start_point,
                end_point=end_point,
                start_marker_index=start_marker,
                end_marker_index=end_marker,
                start_extension_indices=start_extensions,
                end_extension_indices=end_extensions,
                is_axis_derived=(
                    _is_axis_derived(axis, start_point, grid_lines)
                    or _is_axis_derived(axis, end_point, grid_lines)
                ),
            )
        )

    return results
