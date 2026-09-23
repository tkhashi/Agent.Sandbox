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
    collect_axis_aligned_lines,
    find_arcs,
    find_evenly_spaced_runs,
    merge_axis_fragments,
    prune_to_closed_loops,
    split_by_relative_jump,
)

_DASH_LENGTH_RATIO_THRESHOLD = 2.5  # 「短い(点線の点)」「長い(通常の線)」を分ける倍率ジャンプ閾値
_DASH_CHAIN_TOLERANCE = 1.0  # pt。点線の断片同士が接続しているとみなす許容値
_MIN_LOOP_MEMBERS = 4  # 閉じた点線とみなすための最低本数

_ARC_RADIUS_RATIO_THRESHOLD = 2.5  # 装飾的な小さい円弧とドア記号を分ける倍率ジャンプ閾値(ADR0008と同じ考え方)

_GAP_BUCKET_RATIO_TOLERANCE = 1.3  # ハッチングの間隔を同一グループとみなす相対許容差


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
    records: list[VectorRecord], excluded_indices: frozenset[int]
) -> tuple[DashedClosedLoop, ...]:
    candidates: list[tuple[int, tuple[float, float], tuple[float, float], float]] = []
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
        candidates.append((index, a, b, length))

    if len(candidates) < _MIN_LOOP_MEMBERS:
        return ()

    # 点線の「点」は通常の線分より明らかに短い。この境界を絶対値ではなく
    # データ分布の自然な倍率ジャンプから求める(境界が無ければ検出しない)。
    boundary = split_by_relative_jump([c[3] for c in candidates], _DASH_LENGTH_RATIO_THRESHOLD)
    if boundary is None:
        return ()
    dash_candidates = [c for c in candidates if c[3] <= boundary]
    if len(dash_candidates) < _MIN_LOOP_MEMBERS:
        return ()

    n = len(dash_candidates)
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

    # 空間ハッシュ(グリッド)で近傍探索を絞り込み、総当たりの組み合わせ
    # 爆発を防ぐ(壁分類で学んだのと同じ教訓)。
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (_index, a, b, _length) in enumerate(dash_candidates):
        for p in (a, b):
            buckets.setdefault(_bucket(p, _DASH_CHAIN_TOLERANCE), []).append(i)

    for i, (_index, a, b, _length) in enumerate(dash_candidates):
        for p in (a, b):
            bx, by = _bucket(p, _DASH_CHAIN_TOLERANCE)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in buckets.get((bx + dx, by + dy), []):
                        if j == i:
                            continue
                        for q in (dash_candidates[j][1], dash_candidates[j][2]):
                            if math.hypot(p[0] - q[0], p[1] - q[1]) <= _DASH_CHAIN_TOLERANCE:
                                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    loops: list[DashedClosedLoop] = []
    for member_positions in groups.values():
        if len(member_positions) < _MIN_LOOP_MEMBERS:
            continue
        edges = [(dash_candidates[i][1], dash_candidates[i][2]) for i in member_positions]
        survived = prune_to_closed_loops(edges, _DASH_CHAIN_TOLERANCE)
        # 成分内に行き止まり(枝刈りで除去される辺)が1本でもあれば、
        # 「閉じた」点線とは言えないため採用しない。
        if not all(survived):
            continue
        line_indices = tuple(sorted(dash_candidates[i][0] for i in member_positions))
        loops.append(DashedClosedLoop(line_indices=line_indices))

    return tuple(loops)


def _detect_doors(records: list[VectorRecord], excluded_indices: frozenset[int]) -> tuple[DoorSymbol, ...]:
    raw = [arc for arc in find_arcs(records) if arc[0] not in excluded_indices]
    if not raw:
        return ()

    radii = [radius for _index, _center, radius, _s, _e in raw]
    boundary = split_by_relative_jump(radii, _ARC_RADIUS_RATIO_THRESHOLD)

    doors: list[DoorSymbol] = []
    for index, center, radius, _start_angle, _end_angle in raw:
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
        # まとめる(相対許容差によるグリーディなクラスタリング。ユーザー
        # 確認済み: 線幅・間隔が一致すれば離れた場所にあっても同一グループ)。
        sorted_instances = sorted(group_instances, key=lambda instance: instance.gap)
        cluster = [sorted_instances[0]]
        for instance in sorted_instances[1:]:
            ratio = instance.gap / cluster[-1].gap if cluster[-1].gap > 0 else None
            if ratio is not None and ratio <= _GAP_BUCKET_RATIO_TOLERANCE:
                cluster.append(instance)
            else:
                groups.append(_finalize_hatch_group(group_id, linewidth, cluster))
                group_id += 1
                cluster = [instance]
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
        dashed_closed_loops=_detect_dashed_closed_loops(records, excluded_indices),
        doors=_detect_doors(records, excluded_indices),
        hatch_groups=_detect_hatching(records, excluded_indices),
    )
