"""壁(内壁)の分類。

壁は図面ごとに描き方の詳細度が変わり、他の分類タスクのように決定的な
条件だけでは判定しきれない。そのため以下の2段構成を取る(詳細は
docs/adr/0008-wall-classification.mdを参照):

1. **候補生成**: 同一線幅・平行で、一定間隔(壁厚として妥当な範囲)を
   保って伸びる2本の`line`を`WallPairSegment`とする。通り芯・寸法線を
   構成する`line`は候補から除外する。さらに、壁は必ず閉じた輪郭(部屋を
   一周する多角形)の一部であるという制約を用い、他の壁と繋がって
   閉ループを構成しない(行き止まりの)候補は除外する。残った候補を
   (方向転換を許容して)連結し`WallRun`とする
2. **スコアリング**: 各`WallRun`に対し、以下5つの根拠を加点する
   - 寸法線・仮想寸法線との中心対称性
   - 部屋名文字列による四方の囲い込み
   - ドア記号(扇形)の連なり・開口方向
   - 壁符号(「W1」等)・「壁」という文字列の近接
   - 並行線としての連続長・厚みの安定性
   加点の合計スコアが、スコア分布から導出した閾値以上であれば壁と判定する

要素の「並行線としての連続長・厚みの安定性」は候補生成そのものの基準で
あると同時に、「より長く・より厚みが安定しているほど高スコア」という
スコア要素としても働く(候補生成とスコアリングの両方を兼ねる、という
設計上の割り切り)。
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Literal

from ..schema import VectorRecord
from .dimension import DimensionSegment
from .geometry import (
    collect_axis_aligned_lines,
    collect_char_runs,
    find_arcs,
    merge_axis_fragments,
    prune_to_closed_loops,
    split_by_relative_jump,
)
from .grid import GridLine

_LINEWIDTH_TOLERANCE = 0.01  # pt。線幅が「同一」とみなす許容差
_MIN_OVERLAP_RATIO = 0.5  # ペアとみなすための、短い方の区間長に対する重なり比率の最低値
_MIN_THICKNESS = 0.5  # pt。ほぼ0の間隔(重複線)は壁の両面とみなさない
_MIN_LENGTH_TO_THICKNESS_RATIO = 0.3  # 壁の長さは厚みの最低何倍以上あるべきか
_THICKNESS_RATIO_THRESHOLD = 2.5  # 壁厚として妥当な間隔とそれ以外を分ける倍率ジャンプ閾値
_MAX_WALL_THICKNESS_PAGE_RATIO = 0.05  # ページ対角線に対する、壁厚として現実的にありえる上限比率
# (「壁が極端に厚いことはない」という制約を、絶対pt値ではなくページ寸法比の
# 上限として表す。この上限の範囲内で、さらにデータ分布の自然な倍率ジャンプ
# (_THICKNESS_RATIO_THRESHOLD)から実際の壁厚上限を絞り込む)
_MERGE_GAP_TOLERANCE = 0.75  # pt。同一座標・同一線幅の断片を1本の面としてまとめる際の許容ギャップ
# (通り芯番号の一点鎖線等、同一座標に大量の短い断片が並ぶケースで、
# 断片同士を総当たりでペア化すると組み合わせ爆発を起こすため、まず
# 隣接タッチ判定で1本の「面」候補にまとめてから、面同士でペアを探す)

_CHAIN_TOLERANCE = 1.0  # pt。区間の端点同士が接続しているとみなす許容値
_THICKNESS_CONTINUITY_TOLERANCE = 1.0  # pt。連結時に許容する厚みの差

_COORDINATE_TOLERANCE = 0.75  # pt。寸法線中心・仮想延長線との一致とみなす許容値
_MAX_DIMENSION_MATCHES = 3  # 中心対称性の加点が頭打ちになる一致件数
_SCORE_DIMENSION_CENTER = 1.0

_ROOM_KEYWORDS = (
    "室", "ルーム", "トイレ", "便所", "オフィス", "ホール", "廊下", "場", "エリア", "PS", "EPS",
)
_ROOM_LABEL_GAP_FACTOR = 0.6
_ROOM_RAY_SEARCH_FACTOR = 20.0  # フォントサイズに対するレイキャスト最大距離の倍率
_SCORE_ROOM_ENCLOSURE = 1.0

_ARC_PIVOT_TOLERANCE = 2.0  # pt。円弧の中心が壁センターライン上とみなす許容値
_ARC_RADIUS_RATIO_THRESHOLD = 2.5  # 装飾的な小さい円弧とドア記号を分ける倍率ジャンプ閾値
_MAX_DOOR_MATCHES = 3  # ドア隣接の加点が頭打ちになる一致件数(1つのWallRunあたり)
_SCORE_DOOR_ADJACENCY = 1.0
_SCORE_DOOR_DIRECTIONAL_BONUS = 0.5

_WALL_LABEL_PATTERN = re.compile(r"^(W-?[0-9]{1,3}|壁)$")
_WALL_LABEL_GAP_FACTOR = 0.6
_WALL_LABEL_SEARCH_FACTOR = 15.0  # フォントサイズに対する近傍探索距離の倍率
_SCORE_WALL_LABEL = 1.0

_WALL_LENGTH_SCORE_RATIO = 0.15  # ページ対角線に対する、連続長スコアが頭打ちになる比率
_SCORE_PARALLEL_LENGTH_MAX = 1.0
_SCORE_PARALLEL_CONSISTENCY_MAX = 1.0

_WALL_SCORE_THRESHOLD_RATIO = 1.0  # 自然な境界が見つからない場合のフォールバック閾値
# (2-core枝刈りを通過した候補は、厚みの安定性スコアだけで最大1.0点持つため、
# 候補生成の構造的な足切りを主とし、フォールバック閾値はそれを下回らない
# 水準に設定する)
_WALL_SCORE_JUMP_RATIO = 1.8  # スコア分布から閾値を導出する際の倍率ジャンプ閾値


@dataclass(frozen=True)
class WallPairSegment:
    """壁の片方の直線区間ペア(内壁の両面)。"""

    axis: Literal["horizontal", "vertical"]
    coordinate_a: float
    coordinate_b: float
    lo: float
    hi: float
    line_indices_a: tuple[int, ...]
    line_indices_b: tuple[int, ...]
    linewidth: float | None
    thickness: float

    @property
    def centerline(self) -> float:
        return (self.coordinate_a + self.coordinate_b) / 2

    def endpoint(self, along: float) -> tuple[float, float]:
        return (along, self.centerline) if self.axis == "horizontal" else (self.centerline, along)


@dataclass(frozen=True)
class WallRun:
    """方向転換を許容して連結された壁区間の連なり(内壁候補)。"""

    segments: tuple[WallPairSegment, ...]
    line_indices: tuple[int, ...]
    polygon: tuple[tuple[float, float], ...]
    score: float
    score_components: dict[str, float]
    is_wall: bool


def _collect_parallel_pairs(
    records: list[VectorRecord],
    excluded_indices: set[int],
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[WallPairSegment]:
    aligned = collect_axis_aligned_lines(records)
    for items in aligned.values():
        items[:] = [item for item in items if item["index"] not in excluded_indices]

    thickness_samples: list[float] = []
    raw_pairs: list[tuple[Literal["horizontal", "vertical"], dict, dict, float]] = []

    # 「壁が極端に厚いことはない」という制約を、まずページ寸法比の上限
    # (_MAX_WALL_THICKNESS_PAGE_RATIO)として表す。座標でソート済みの配列上
    # では厚み(b.coord - a.coord)が単調増加するため、この上限を超えた
    # 時点でこのaについての探索を打ち切る(計算量を抑える効果も兼ねる)。
    page_diagonal = (
        math.hypot(page_width, page_height) if page_width is not None and page_height is not None else None
    )
    precheck_cap = page_diagonal * _MAX_WALL_THICKNESS_PAGE_RATIO if page_diagonal else None

    # 各面は、自分より座標が大きい側にある「最初に条件を満たす面」1つとだけ
    # ペアになりうる(nearest-neighbor)。壁の両面は必ずちょうど2本の面
    # (自分自身と、その真向かいの1本)だけで構成されるため、これで十分
    # 表現できる。全組み合わせを総当たりで見てしまうと、フローリング材の
    # ハッチング等、同一線幅の面が多数並ぶ箇所で組み合わせが爆発し、
    # かつ後段の閉ループ判定を欺くほど密なグラフができてしまう。
    for axis, items in aligned.items():
        faces = sorted(merge_axis_fragments(items, _MERGE_GAP_TOLERANCE), key=lambda f: f["coord"])
        for i, a in enumerate(faces):
            for b in faces[i + 1 :]:
                thickness = b["coord"] - a["coord"]
                if precheck_cap is not None and thickness > precheck_cap:
                    break
                if thickness <= 0:
                    continue
                if a["linewidth"] is None or b["linewidth"] is None:
                    continue
                if abs(a["linewidth"] - b["linewidth"]) > _LINEWIDTH_TOLERANCE:
                    continue
                overlap_lo = max(a["lo"], b["lo"])
                overlap_hi = min(a["hi"], b["hi"])
                overlap = overlap_hi - overlap_lo
                shorter_span = min(a["hi"] - a["lo"], b["hi"] - b["lo"])
                if shorter_span <= 0 or overlap / shorter_span < _MIN_OVERLAP_RATIO:
                    continue
                if thickness < _MIN_THICKNESS:
                    continue
                # 壁は厚みに対してある程度以上の長さを持つはずで、厚みより
                # 極端に短い区間(点状のティックマーク・寸法端部の装飾等)は
                # 壁ではありえないため除外する
                if overlap < thickness * _MIN_LENGTH_TO_THICKNESS_RATIO:
                    continue
                thickness_samples.append(thickness)
                raw_pairs.append((axis, a, b, thickness))
                break

    if not raw_pairs:
        return []

    # 壁厚として妥当な間隔の上限を、データ分布の自然な倍率ジャンプから
    # さらに絞り込む。境界が見つからない場合は、ページ寸法比の上限
    # (precheck_cap)のみで判定する。
    thickness_limit = split_by_relative_jump(thickness_samples, _THICKNESS_RATIO_THRESHOLD)

    pairs: list[WallPairSegment] = []
    for axis, a, b, thickness in raw_pairs:
        if thickness_limit is not None and thickness > thickness_limit:
            continue
        overlap_lo = max(a["lo"], b["lo"])
        overlap_hi = min(a["hi"], b["hi"])
        pairs.append(
            WallPairSegment(
                axis=axis,
                coordinate_a=a["coord"],
                coordinate_b=b["coord"],
                lo=overlap_lo,
                hi=overlap_hi,
                line_indices_a=a["indices"],
                line_indices_b=b["indices"],
                linewidth=a["linewidth"],
                thickness=thickness,
            )
        )
    return pairs


def _prune_to_closed_loops(pairs: list[WallPairSegment]) -> list[WallPairSegment]:
    """他の壁候補と繋がって閉ループ(部屋を一周する輪郭)を構成しない候補を除外する。

    壁は必ず閉じた直線に囲まれている、という制約を反映する。汎用の
    `geometry.prune_to_closed_loops`(センターライン端点グラフの2-core抽出=
    leaf-pruning)を`WallPairSegment`向けに適用するラッパー。

    フローリング材等のハッチングは、隣接する線同士が独立した平行線対を
    作るだけで他の対と端点を共有しないため、この枝刈りで自然に除去される。
    """
    if not pairs:
        return []
    edges = [_segment_endpoints(pair) for pair in pairs]
    survived = prune_to_closed_loops(edges, _CHAIN_TOLERANCE)
    return [pair for pair, alive in zip(pairs, survived) if alive]


def _segment_endpoints(segment: WallPairSegment) -> tuple[tuple[float, float], tuple[float, float]]:
    return segment.endpoint(segment.lo), segment.endpoint(segment.hi)


def _chain_pair_segments(pairs: list[WallPairSegment]) -> list[list[WallPairSegment]]:
    """センターラインの端点が近接する`WallPairSegment`を、軸をまたいで連結する。

    厚みが大きく異なる場合は別の壁とみなし連結しない(方向転換は許容するが、
    壁厚が途中で跳ぶのは別の壁が偶然接しているだけの可能性が高いため)。
    """
    n = len(pairs)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    endpoints = [_segment_endpoints(p) for p in pairs]
    for i in range(n):
        for j in range(i + 1, n):
            if abs(pairs[i].thickness - pairs[j].thickness) > _THICKNESS_CONTINUITY_TOLERANCE:
                continue
            connected = False
            for pi in endpoints[i]:
                for pj in endpoints[j]:
                    if math.hypot(pi[0] - pj[0], pi[1] - pj[1]) <= _CHAIN_TOLERANCE:
                        connected = True
                        break
                if connected:
                    break
            if connected:
                union(i, j)

    groups: dict[int, list[WallPairSegment]] = {}
    for i, pair in enumerate(pairs):
        groups.setdefault(find(i), []).append(pair)
    return list(groups.values())


def _order_chain(segments: list[WallPairSegment]) -> list[WallPairSegment]:
    """連結成分内のセグメントを、端点の接続関係に沿った順序に並べ替える。"""
    remaining = list(segments)
    ordered = [remaining.pop(0)]
    while remaining:
        last_a, last_b = _segment_endpoints(ordered[-1])
        best_index, best_dist, best_flip = None, None, False
        for i, seg in enumerate(remaining):
            a, b = _segment_endpoints(seg)
            for other_end, flip in ((a, False), (b, True)):
                dist = math.hypot(other_end[0] - last_b[0], other_end[1] - last_b[1])
                if best_dist is None or dist < best_dist:
                    best_dist, best_index, best_flip = dist, i, flip
        if best_index is None:
            break
        seg = remaining.pop(best_index)
        ordered.append(seg)
    return ordered


def _build_polygon(segments: list[WallPairSegment]) -> tuple[tuple[float, float], ...]:
    """連結順に沿って両面をオフセットしたリボン状ポリゴンを生成する。

    屈曲部の結合は単純な頂点列の連結(簡易マイター)に留め、rectangle-union
    のような一般的なポリゴン演算は行わない(依存追加を避けるため)。
    非直交な屈曲では頂点がわずかに自己交差しうるが、本図面の壁はほぼ
    直交であるため実用上の問題は小さいと判断する。
    """
    ordered = _order_chain(list(segments))
    face_a: list[tuple[float, float]] = []
    face_b: list[tuple[float, float]] = []
    for seg in ordered:
        p_lo = seg.endpoint(seg.lo)
        p_hi = seg.endpoint(seg.hi)
        if seg.axis == "horizontal":
            a_lo = (p_lo[0], seg.coordinate_a)
            a_hi = (p_hi[0], seg.coordinate_a)
            b_lo = (p_lo[0], seg.coordinate_b)
            b_hi = (p_hi[0], seg.coordinate_b)
        else:
            a_lo = (seg.coordinate_a, p_lo[1])
            a_hi = (seg.coordinate_a, p_hi[1])
            b_lo = (seg.coordinate_b, p_lo[1])
            b_hi = (seg.coordinate_b, p_hi[1])
        face_a.extend([a_lo, a_hi])
        face_b.extend([b_lo, b_hi])
    return tuple(face_a + list(reversed(face_b)))


@dataclass(frozen=True)
class RoomLabel:
    text: str
    center: tuple[float, float]
    font_size: float
    char_indices: tuple[int, ...]


def _find_room_labels(records: list[VectorRecord]) -> list[RoomLabel]:
    runs = collect_char_runs(
        records,
        lambda text: any(
            "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "A" <= ch <= "Z" for ch in text
        ),
        gap_factor=_ROOM_LABEL_GAP_FACTOR,
    )
    labels: list[RoomLabel] = []
    for run in runs:
        text = "".join(item["text"] for item in run)
        if not any(keyword in text for keyword in _ROOM_KEYWORDS):
            continue
        xs = [item["x0"] for item in run] + [item["x1"] for item in run]
        tops = [item["top"] for item in run] + [item["bottom"] for item in run]
        font_size = sum(item["font_size"] for item in run) / len(run)
        labels.append(
            RoomLabel(
                text=text,
                center=((min(xs) + max(xs)) / 2, (min(tops) + max(tops)) / 2),
                font_size=font_size,
                char_indices=tuple(item["index"] for item in run),
            )
        )
    return labels


def _raycast_hit(
    label_center: tuple[float, float],
    direction: Literal["up", "down", "left", "right"],
    pairs: list[WallPairSegment],
    max_distance: float,
) -> WallPairSegment | None:
    cx, cy = label_center
    best: tuple[float, WallPairSegment] | None = None
    for pair in pairs:
        if direction in ("left", "right"):
            if pair.axis != "vertical":
                continue
            if not (pair.lo <= cy <= pair.hi):
                continue
            dist = pair.centerline - cx if direction == "right" else cx - pair.centerline
        else:
            if pair.axis != "horizontal":
                continue
            if not (pair.lo <= cx <= pair.hi):
                continue
            dist = pair.centerline - cy if direction == "down" else cy - pair.centerline
        if dist <= 0 or dist > max_distance:
            continue
        if best is None or dist < best[0]:
            best = (dist, pair)
    return best[1] if best else None


def _score_room_enclosure(
    pair_to_run: dict[int, int],
    run_scores: list[dict[str, float]],
    pairs: list[WallPairSegment],
    room_labels: list[RoomLabel],
) -> None:
    for label in room_labels:
        max_distance = label.font_size * _ROOM_RAY_SEARCH_FACTOR
        for direction in ("up", "down", "left", "right"):
            hit = _raycast_hit(label.center, direction, pairs, max_distance)
            if hit is None:
                continue
            run_index = pair_to_run[id(hit)]
            run_scores[run_index]["room_enclosure"] = (
                run_scores[run_index].get("room_enclosure", 0.0) + _SCORE_ROOM_ENCLOSURE
            )


def _is_dashed_arc(records: list[VectorRecord], center: tuple[float, float], radius: float) -> bool:
    """円弧の周囲に、等間隔・同程度の長さの短い線分が並んでいるかを見て点線かを判定する。

    一点鎖線の「長短交互」とは異なり、ドアの点線は同じ長さの断片が
    規則的に並ぶだけのため、`find_chain_runs`とは別の単純な基準
    (短い線分が複数、近い間隔で並んでいるか)で判定する。
    """
    cx, cy = center
    nearby_lengths: list[float] = []
    for record in records:
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
        mx, my = (x0 + x1) / 2, (top + bottom) / 2
        if math.hypot(mx - cx, my - cy) > radius * 1.5:
            continue
        length = math.hypot(x1 - x0, top - bottom)
        if length > 0:
            nearby_lengths.append(length)
    return len(nearby_lengths) >= 3


@dataclass(frozen=True)
class DoorArc:
    arc_index: int
    center: tuple[float, float]
    radius: float
    is_dashed: bool


def _find_door_arcs(records: list[VectorRecord]) -> list[DoorArc]:
    raw = find_arcs(records)
    if not raw:
        return []

    # 水栓・取っ手等の装飾的な小さいアイコンの円弧は、ドアの扇形より
    # 明らかに半径が小さい。絶対値を決め打ちせず、半径分布の自然な
    # 倍率ジャンプで「小さい装飾円弧」の集団を除外する(見つからない
    # 場合は安全側に倒し、全て候補として残す)。
    radii = [radius for _index, _center, radius, _s, _e in raw]
    boundary = split_by_relative_jump(radii, _ARC_RADIUS_RATIO_THRESHOLD)

    arcs: list[DoorArc] = []
    for index, center, radius, _start_angle, _end_angle in raw:
        if boundary is not None and radius < boundary:
            continue
        arcs.append(
            DoorArc(
                arc_index=index,
                center=center,
                radius=radius,
                is_dashed=_is_dashed_arc(records, center, radius),
            )
        )
    return arcs


def _score_door_adjacency(
    pairs: list[WallPairSegment],
    pair_to_run: dict[int, int],
    run_scores: list[dict[str, float]],
    door_arcs: list[DoorArc],
) -> None:
    matches_by_run: dict[int, int] = {}
    for arc in door_arcs:
        cx, cy = arc.center
        for pair in pairs:
            along = cx if pair.axis == "vertical" else cy
            if not (pair.lo - _ARC_PIVOT_TOLERANCE <= along <= pair.hi + _ARC_PIVOT_TOLERANCE):
                continue
            perpendicular = cy if pair.axis == "vertical" else cx
            if abs(perpendicular - pair.centerline) > _ARC_PIVOT_TOLERANCE:
                continue
            run_index = pair_to_run[id(pair)]
            if matches_by_run.get(run_index, 0) >= _MAX_DOOR_MATCHES:
                continue
            matches_by_run[run_index] = matches_by_run.get(run_index, 0) + 1
            bonus = _SCORE_DOOR_DIRECTIONAL_BONUS if arc.is_dashed else 0.0
            run_scores[run_index]["door_adjacency"] = (
                run_scores[run_index].get("door_adjacency", 0.0) + _SCORE_DOOR_ADJACENCY + bonus
            )


@dataclass(frozen=True)
class WallLabel:
    text: str
    center: tuple[float, float]
    font_size: float
    char_indices: tuple[int, ...]


def _find_wall_labels(records: list[VectorRecord]) -> list[WallLabel]:
    runs = collect_char_runs(
        records,
        lambda text: text in ("W", "壁", "-") or text.isdigit(),
        gap_factor=_WALL_LABEL_GAP_FACTOR,
    )
    labels: list[WallLabel] = []
    for run in runs:
        text = "".join(item["text"] for item in run)
        if not _WALL_LABEL_PATTERN.match(text):
            continue
        xs = [item["x0"] for item in run] + [item["x1"] for item in run]
        tops = [item["top"] for item in run] + [item["bottom"] for item in run]
        font_size = sum(item["font_size"] for item in run) / len(run)
        labels.append(
            WallLabel(
                text=text,
                center=((min(xs) + max(xs)) / 2, (min(tops) + max(tops)) / 2),
                font_size=font_size,
                char_indices=tuple(item["index"] for item in run),
            )
        )
    return labels


def _nearest_pair(
    label_center: tuple[float, float], pairs: list[WallPairSegment], max_distance: float
) -> WallPairSegment | None:
    cx, cy = label_center
    best: tuple[float, WallPairSegment] | None = None
    for pair in pairs:
        along = cx if pair.axis == "vertical" else cy
        if not (pair.lo - _COORDINATE_TOLERANCE <= along <= pair.hi + _COORDINATE_TOLERANCE):
            continue
        perpendicular = cy if pair.axis == "vertical" else cx
        dist = abs(perpendicular - pair.centerline)
        if dist > max_distance:
            continue
        if best is None or dist < best[0]:
            best = (dist, pair)
    return best[1] if best else None


def _score_wall_label(
    pairs: list[WallPairSegment],
    pair_to_run: dict[int, int],
    run_scores: list[dict[str, float]],
    wall_labels: list[WallLabel],
) -> None:
    for label in wall_labels:
        max_distance = label.font_size * _WALL_LABEL_SEARCH_FACTOR
        hit = _nearest_pair(label.center, pairs, max_distance)
        if hit is None:
            continue
        run_index = pair_to_run[id(hit)]
        run_scores[run_index]["wall_label"] = (
            run_scores[run_index].get("wall_label", 0.0) + _SCORE_WALL_LABEL
        )


def _dimension_centers(
    dimension_segments: list[DimensionSegment],
) -> list[tuple[Literal["horizontal", "vertical"], float, float]]:
    """寸法線の「中心点」「両端点から直交方向に伸ばした仮想延長線の基準座標」を集める。

    戻り値は`(壁の軸, 壁センターラインが一致すべき座標, along方向の座標)`。
    壁の軸は寸法線の軸と直交する(寸法線は壁と壁の間の距離を測るため、
    それを横切る壁は寸法線と直交方向に立っている)。
    """
    anchors: list[tuple[Literal["horizontal", "vertical"], float, float]] = []
    for seg in dimension_segments:
        wall_axis: Literal["horizontal", "vertical"] = "vertical" if seg.axis == "horizontal" else "horizontal"
        # 寸法線自身の中心点: 壁がこの位置を横切っている場合に一致しうる
        mid_x = (seg.start_point[0] + seg.end_point[0]) / 2
        mid_y = (seg.start_point[1] + seg.end_point[1]) / 2
        along = mid_x if wall_axis == "horizontal" else mid_y
        anchors.append((wall_axis, seg.coordinate, along))
        # 両端点からの仮想延長線: 端点を通り、寸法線自身の軸方向に伸びる
        for point in (seg.start_point, seg.end_point):
            along2 = point[0] if seg.axis == "horizontal" else point[1]
            anchors.append((wall_axis, along2, seg.coordinate))
    return anchors


def _score_dimension_symmetry(
    pairs: list[WallPairSegment],
    pair_to_run: dict[int, int],
    run_scores: list[dict[str, float]],
    dimension_segments: list[DimensionSegment],
) -> None:
    anchors = _dimension_centers(dimension_segments)
    for pair in pairs:
        matches = 0
        for axis, coordinate, along in anchors:
            if axis != pair.axis:
                continue
            if abs(pair.centerline - coordinate) > _COORDINATE_TOLERANCE:
                continue
            if not (pair.lo - _COORDINATE_TOLERANCE <= along <= pair.hi + _COORDINATE_TOLERANCE):
                continue
            matches += 1
            if matches >= _MAX_DIMENSION_MATCHES:
                break
        if matches == 0:
            continue
        run_index = pair_to_run[id(pair)]
        run_scores[run_index]["dimension_symmetry"] = (
            run_scores[run_index].get("dimension_symmetry", 0.0)
            + _SCORE_DIMENSION_CENTER * min(matches, _MAX_DIMENSION_MATCHES)
        )


def _score_parallel_continuity(
    segments: tuple[WallPairSegment, ...], page_width: float | None, page_height: float | None
) -> float:
    total_length = sum(seg.hi - seg.lo for seg in segments)
    length_score = 0.0
    if page_width is not None and page_height is not None:
        page_diagonal = math.hypot(page_width, page_height)
        if page_diagonal > 0:
            length_score = min(
                _SCORE_PARALLEL_LENGTH_MAX, total_length / (page_diagonal * _WALL_LENGTH_SCORE_RATIO)
            )

    thicknesses = [seg.thickness for seg in segments]
    consistency_score = _SCORE_PARALLEL_CONSISTENCY_MAX
    if len(thicknesses) >= 2:
        mean_thickness = statistics.mean(thicknesses)
        if mean_thickness > 0:
            stddev = statistics.pstdev(thicknesses)
            consistency_score = _SCORE_PARALLEL_CONSISTENCY_MAX * max(0.0, 1.0 - min(1.0, stddev / mean_thickness))

    return length_score + consistency_score


def _excluded_line_indices(grid_lines: list[GridLine], dimension_segments: list[DimensionSegment]) -> set[int]:
    """通り芯・寸法線を構成する`line`のインデックスを集める(壁候補から除外するため)。"""
    excluded: set[int] = set()
    for grid_line in grid_lines:
        excluded.update(grid_line.segment_indices)
    for segment in dimension_segments:
        excluded.update(segment.line_indices)
        excluded.update(segment.start_extension_indices)
        excluded.update(segment.end_extension_indices)
    return excluded


def classify_wall_lines(
    records: list[VectorRecord],
    dimension_segments: list[DimensionSegment],
    grid_lines: list[GridLine] | None = None,
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[WallRun]:
    excluded_indices = _excluded_line_indices(grid_lines or [], dimension_segments)
    pairs = _collect_parallel_pairs(records, excluded_indices, page_width, page_height)
    if not pairs:
        return []

    # 壁は必ず閉じた直線(部屋を一周する輪郭)の一部である、という制約を
    # 反映する。他の候補と繋がって閉ループを構成しない(行き止まりの)
    # 候補はここで除外する。
    pairs = _prune_to_closed_loops(pairs)
    if not pairs:
        return []

    groups = _chain_pair_segments(pairs)

    pair_to_run: dict[int, int] = {}
    for run_index, group in enumerate(groups):
        for pair in group:
            pair_to_run[id(pair)] = run_index

    run_scores: list[dict[str, float]] = [{} for _ in groups]

    room_labels = _find_room_labels(records)
    _score_room_enclosure(pair_to_run, run_scores, pairs, room_labels)

    door_arcs = _find_door_arcs(records)
    _score_door_adjacency(pairs, pair_to_run, run_scores, door_arcs)

    _score_dimension_symmetry(pairs, pair_to_run, run_scores, dimension_segments)

    wall_labels = _find_wall_labels(records)
    _score_wall_label(pairs, pair_to_run, run_scores, wall_labels)

    for run_index, group in enumerate(groups):
        run_scores[run_index]["parallel_continuity"] = _score_parallel_continuity(
            tuple(group), page_width, page_height
        )

    totals = [sum(components.values()) for components in run_scores]
    threshold = split_by_relative_jump(
        [t for t in totals if t > 0], _WALL_SCORE_JUMP_RATIO
    )
    if threshold is None:
        threshold = _WALL_SCORE_THRESHOLD_RATIO

    runs: list[WallRun] = []
    for run_index, group in enumerate(groups):
        line_indices = tuple(
            sorted({idx for pair in group for idx in (*pair.line_indices_a, *pair.line_indices_b)})
        )
        score = totals[run_index]
        components = run_scores[run_index]
        runs.append(
            WallRun(
                segments=tuple(group),
                line_indices=line_indices,
                polygon=_build_polygon(group),
                score=score,
                score_components=dict(components),
                # 「閉じた直線に囲まれている」(候補生成時の2-core枝刈り)
                # という構造的な制約を主な足切りとし、他の4根拠は付加的な
                # スコアとして扱う(閉ループ制約の導入により、平行線だけの
                # ハッチング等は候補の時点で排除されるため)。
                is_wall=score >= threshold,
            )
        )
    return runs
