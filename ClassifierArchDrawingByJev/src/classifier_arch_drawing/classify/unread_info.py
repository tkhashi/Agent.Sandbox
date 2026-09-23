"""平面図を読み取る上で不要な情報の分類。

このタスクは「全ての不要情報を網羅する」ことを目的とせず、**検出パターンが
今後も増え続ける**ことを前提に設計する。各パターンは他のパターンを一切
知らない独立した検出関数として実装し、`classify_unread_info`はそれらを
呼び出して結果をまとめるだけにする。新しいパターンを追加する場合は、
検出関数を1つ書いて`classify_unread_info`の呼び出しリストに1行足すだけで
よい(詳細はdocs/adr/0010-unread-info-classification.mdを参照)。

初版で対応するパターン:

1. **点線で閉じた線**: 見上げ部材・置き場等を示す、点線(短い線分の連なり)で
   描かれた閉じた輪郭。読み取り不要な純粋な除外情報として扱う
2. **ハッチング**: 同一線幅の平行線が4本以上、ほぼ等間隔に並ぶもの(直線・
   点線いずれも対象、`curve`は対象外)。部材の材質・材種を表すため、
   線幅・間隔が一致するものを同一グループとして情報を保持する(除外しない)
3. **扇形(ドア記号)**: 除外せず、扉として別データで保持する
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal

from ..schema import VectorRecord
from .geometry import (
    cluster_by_relative_jumps,
    collect_axis_aligned_lines,
    find_arcs,
    find_evenly_spaced_runs,
    merge_axis_fragments,
    split_by_relative_jump,
)

_MAX_DASH_LENGTH_PAGE_RATIO = 0.003  # ページ対角線に対する、点線の点として現実的にありえる長さの上限比率
_DASH_GAP_TO_LENGTH_RATIO = 0.8  # 点線の断片同士を接続する際の許容距離(断片自身の長さに対する倍率)
# (点線の「点」と「点」の間の隙間は、点自体の長さと同程度になることが多い
# (実測: 断片長2.8〜4.3ptに対し隙間約2.8〜2.9pt)。固定の絶対pt値では
# 図面ごとの点線の粗密に対応できないため、断片自身の長さに対する相対値で表す)
_MIN_LOOP_MEMBERS = 4  # 閉じた点線とみなすための最低本数

_ARC_RADIUS_RATIO_THRESHOLD = 2.5  # 装飾的な小さい円弧とドア記号を分ける倍率ジャンプ閾値(ADR0008と同じ考え方)
_ARC_CLUSTER_RADIUS_RATIO_THRESHOLD = 1.3  # 弧同士を「近い半径」とみなす倍率許容差
_ARC_CLUSTER_PROXIMITY_RATIO = 10.0  # 弧同士を「近接」とみなす、半径に対する中心間距離の倍率
_MIN_DECORATIVE_CLUSTER_SIZE = 3  # この本数以上、近い半径の弧が密集していたら装飾記号とみなす

_GAP_CLUSTER_RATIO_THRESHOLD = 1.3  # ハッチングの間隔を別グループとみなす倍率ジャンプ閾値


@dataclass(frozen=True)
class DashedClosedLoop:
    line_indices: tuple[int, ...]


@dataclass(frozen=True)
class DoorSymbol:
    arc_index: int
    center: tuple[float, float]
    radius: float


@dataclass(frozen=True)
class HatchInstance:
    axis: Literal["horizontal", "vertical"]
    linewidth: float | None
    gap: float
    line_indices: tuple[int, ...]


@dataclass(frozen=True)
class HatchGroup:
    group_id: int
    linewidth: float | None
    gap: float
    instances: tuple[HatchInstance, ...]
    line_indices: tuple[int, ...]


@dataclass(frozen=True)
class UnreadInfoResult:
    dashed_closed_loops: tuple[DashedClosedLoop, ...]
    doors: tuple[DoorSymbol, ...]
    hatch_groups: tuple[HatchGroup, ...]


def _line_endpoints(record: VectorRecord) -> tuple[tuple[float, float], tuple[float, float]] | None:
    x0, x1, top, bottom = record.get("x0"), record.get("x1"), record.get("top"), record.get("bottom")
    if None in (x0, x1, top, bottom):
        return None
    return (x0, top), (x1, bottom)


def _bucket(point: tuple[float, float], tolerance: float) -> tuple[int, int]:
    return (round(point[0] / tolerance), round(point[1] / tolerance))


def _detect_dashed_closed_loops(
    records: list[VectorRecord],
    excluded_indices: frozenset[int],
    page_width: float | None = None,
    page_height: float | None = None,
) -> tuple[DashedClosedLoop, ...]:
    # 点線の「点」候補を、ページ寸法比で見て明らかに小さい線分に絞り込む。
    # ページ全体には様々な用途・縮尺の短い線分(装飾・寸法端部・家具の
    # 細部等)が連続的な長さ分布で混在しており、`split_by_relative_jump`の
    # ような「自然な倍率ジャンプ」による境界を線幅ごとに求めても、そもそも
    # 明確な境界が存在しないことが実データで判明した(点線の点の長さが
    # 他の細部と地続きの分布になっているため)。そのため、ここでは
    # 「閉じた点線を構成しうる程度に小さい」という緩いページ相対の上限
    # (ページ対角線比)だけで候補を絞り、実際に閉ループを構成するかどうか
    # は後段の接続性・2-core判定に委ねる。
    page_diagonal = (
        math.hypot(page_width, page_height) if page_width is not None and page_height is not None else None
    )
    max_dash_length = page_diagonal * _MAX_DASH_LENGTH_PAGE_RATIO if page_diagonal else None

    dash_candidates: list[tuple[int, tuple[float, float], tuple[float, float], float]] = []
    for index, record in enumerate(records):
        if index in excluded_indices or record["object_type"] != "line":
            continue
        endpoints = _line_endpoints(record)
        if endpoints is None:
            continue
        a, b = endpoints
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= 0:
            continue
        if max_dash_length is not None and length > max_dash_length:
            continue
        dash_candidates.append((index, a, b, length))

    if len(dash_candidates) < _MIN_LOOP_MEMBERS:
        return ()

    n = len(dash_candidates)

    # 空間ハッシュ(グリッド)で近傍探索を絞り込み、総当たりの組み合わせ
    # 爆発を防ぐ(壁分類で学んだのと同じ教訓)。バケットサイズは、想定される
    # 最大の接続許容距離(最長の点線断片の長さ×倍率)に合わせる。
    max_gap_tolerance = max(c[3] for c in dash_candidates) * _DASH_GAP_TO_LENGTH_RATIO
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (_index, a, b, _length) in enumerate(dash_candidates):
        for p in (a, b):
            buckets.setdefault(_bucket(p, max_gap_tolerance), []).append(i)

    # 断片同士の隣接関係を、点(座標)ではなく断片(セグメント)自体をノードと
    # するグラフとして記録する。ページ全体では無関係な断片同士が離れた
    # 場所で偶然連結し、巨大な連結成分ができてしまうことがある(実データで
    # 確認済み: 2700本超の1成分)。そのような巨大成分に、本来独立した
    # 小さな閉ループ(例: アイコンの破線枠)が飲み込まれると、成分全体に
    # 行き止まりが1つでもあれば`prune_to_closed_loops`的な判定は成分丸ごと
    # 棄却してしまい、内部に存在する本物の閉ループを見逃す。そのため、
    # 座標ベースの2-coreではなく、断片(セグメント)ベースの隣接グラフに
    # 対してleaf-pruning(次数1以下のノードを繰り返し除去)を直接適用し、
    # 巨大な成分の中に埋もれた本物の閉ループ部分集合だけを正しく抽出する。
    adjacency: list[set[int]] = [set() for _ in range(n)]
    for i, (_index, a, b, length_i) in enumerate(dash_candidates):
        for p in (a, b):
            bx, by = _bucket(p, max_gap_tolerance)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in buckets.get((bx + dx, by + dy), []):
                        if j == i:
                            continue
                        # 接続許容距離は、接続する2断片自身の長さに応じた
                        # 相対値にする(点線の粗密に対応するため)。
                        tolerance = max(length_i, dash_candidates[j][3]) * _DASH_GAP_TO_LENGTH_RATIO
                        for q in (dash_candidates[j][1], dash_candidates[j][2]):
                            if math.hypot(p[0] - q[0], p[1] - q[1]) <= tolerance:
                                adjacency[i].add(j)
                                adjacency[j].add(i)

    alive = [len(adjacency[i]) > 0 for i in range(n)]
    queue = [i for i in range(n) if len(adjacency[i]) <= 1]
    while queue:
        i = queue.pop()
        if not alive[i] or len(adjacency[i]) > 1:
            continue
        alive[i] = False
        for j in list(adjacency[i]):
            adjacency[j].discard(i)
            adjacency[i].discard(j)
            if alive[j] and len(adjacency[j]) <= 1:
                queue.append(j)

    # 枝刈り後に残った隣接関係だけを使って連結成分を再計算する(元の
    # union-findの根で束ねると、枝刈りで切り離された別々の閉ループが
    # 誤って1つにまとめられてしまうため)。
    component_of: dict[int, int] = {}
    groups: dict[int, list[int]] = {}
    for i in range(n):
        if not alive[i] or i in component_of:
            continue
        stack = [i]
        component_of[i] = i
        members = []
        while stack:
            k = stack.pop()
            members.append(k)
            for j in adjacency[k]:
                if j not in component_of:
                    component_of[j] = i
                    stack.append(j)
        groups[i] = members

    loops: list[DashedClosedLoop] = []
    for member_positions in groups.values():
        if len(member_positions) < _MIN_LOOP_MEMBERS:
            continue
        line_indices = tuple(sorted(dash_candidates[i][0] for i in member_positions))
        loops.append(DashedClosedLoop(line_indices=line_indices))

    return tuple(loops)


def _exclude_decorative_arc_clusters(
    raw: list[tuple[int, tuple[float, float], float, float, float]],
) -> list[tuple[int, tuple[float, float], float, float, float]]:
    """半径が近く、近接して密集している弧の集団を装飾記号とみなして除外する。

    断熱材の蛇腹記号(同じ半径の小さい弧が規則的に多数並ぶ)や、角丸四角の
    角(近い半径の弧が数個、近接して並ぶ)は、いずれも「近い半径の弧が
    複数・近接して密集している」という共通の構造的特徴を持つ。本物の扉の
    弧は、周囲に同じ半径の弧を伴わない孤立した存在であるはずなので、
    この基準でクラスタリングし、クラスタサイズが3以上のものを除外する。
    """
    n = len(raw)
    if n < 3:
        return raw

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

    for i in range(n):
        _idx_i, center_i, radius_i, _s_i, _e_i = raw[i]
        for j in range(i + 1, n):
            _idx_j, center_j, radius_j, _s_j, _e_j = raw[j]
            larger = max(radius_i, radius_j)
            smaller = min(radius_i, radius_j)
            if smaller <= 0 or larger / smaller > _ARC_CLUSTER_RADIUS_RATIO_THRESHOLD:
                continue
            distance = math.hypot(center_i[0] - center_j[0], center_i[1] - center_j[1])
            if distance <= larger * _ARC_CLUSTER_PROXIMITY_RATIO:
                union(i, j)

    cluster_sizes: dict[int, int] = {}
    for i in range(n):
        root = find(i)
        cluster_sizes[root] = cluster_sizes.get(root, 0) + 1

    return [raw[i] for i in range(n) if cluster_sizes[find(i)] < _MIN_DECORATIVE_CLUSTER_SIZE]


def _detect_doors(records: list[VectorRecord], excluded_indices: frozenset[int]) -> tuple[DoorSymbol, ...]:
    raw = [arc for arc in find_arcs(records) if arc[0] not in excluded_indices]
    if not raw:
        return ()

    isolated = _exclude_decorative_arc_clusters(raw)
    if not isolated:
        return ()

    radii = [radius for _index, _center, radius, _s, _e in isolated]
    boundary = split_by_relative_jump(radii, _ARC_RADIUS_RATIO_THRESHOLD)

    doors: list[DoorSymbol] = []
    for index, center, radius, _start_angle, _end_angle in isolated:
        if boundary is not None and radius < boundary:
            continue
        doors.append(DoorSymbol(arc_index=index, center=center, radius=radius))
    return tuple(doors)


def _finalize_hatch_group(group_id: int, linewidth: float | None, cluster: list[HatchInstance]) -> HatchGroup:
    gap = statistics.median(instance.gap for instance in cluster)
    line_indices = tuple(sorted({idx for instance in cluster for idx in instance.line_indices}))
    return HatchGroup(
        group_id=group_id,
        linewidth=linewidth,
        gap=gap,
        instances=tuple(cluster),
        line_indices=line_indices,
    )


def _detect_hatching(records: list[VectorRecord], excluded_indices: frozenset[int]) -> tuple[HatchGroup, ...]:
    aligned = collect_axis_aligned_lines(records)
    for items in aligned.values():
        items[:] = [item for item in items if item["index"] not in excluded_indices]

    instances: list[HatchInstance] = []
    for axis, items in aligned.items():
        faces = merge_axis_fragments(items)
        for run in find_evenly_spaced_runs(faces):
            gaps = [run[i + 1]["coord"] - run[i]["coord"] for i in range(len(run) - 1)]
            line_indices = tuple(sorted(idx for face in run for idx in face["indices"]))
            instances.append(
                HatchInstance(
                    axis=axis,  # type: ignore[arg-type]
                    linewidth=run[0]["linewidth"],
                    gap=statistics.median(gaps),
                    line_indices=line_indices,
                )
            )

    if not instances:
        return ()

    by_linewidth: dict[float | None, list[HatchInstance]] = {}
    for instance in instances:
        by_linewidth.setdefault(instance.linewidth, []).append(instance)

    groups: list[HatchGroup] = []
    group_id = 0
    for linewidth, group_instances in by_linewidth.items():
        # 線幅が一致するインスタンスを、間隔(gap)が近いもの同士でさらに
        # まとめる。「隣接する値同士が近ければ連結する」という貪欲な逐次
        # クラスタリングは、両端が大きく乖離した値同士でも中間値を介して
        # 連結されてしまう(チェイニング)欠陥があるため、自然な倍率
        # ジャンプで再帰的に境界を求める`cluster_by_relative_jumps`を使う
        # (ユーザー確認済み: 線幅・間隔が一致すれば離れた場所にあっても
        # 同一グループ)。
        gaps = [instance.gap for instance in group_instances]
        for member_positions in cluster_by_relative_jumps(gaps, _GAP_CLUSTER_RATIO_THRESHOLD):
            cluster = [group_instances[i] for i in member_positions]
            groups.append(_finalize_hatch_group(group_id, linewidth, cluster))
            group_id += 1

    return tuple(groups)


def classify_unread_info(
    records: list[VectorRecord],
    excluded_indices: frozenset[int] = frozenset(),
    page_width: float | None = None,
    page_height: float | None = None,
) -> UnreadInfoResult:
    return UnreadInfoResult(
        dashed_closed_loops=_detect_dashed_closed_loops(records, excluded_indices, page_width, page_height),
        doors=_detect_doors(records, excluded_indices),
        hatch_groups=_detect_hatching(records, excluded_indices),
    )
