"""分類結果の検証用CLI(通り芯・通り芯番号/寸法線・寸法の分類)。

抽出タスク(extract-vectors)とは独立に実行できる。入力は抽出タスクが
出力したJSON(`{"page_width","page_height","linewidth_scale","records"}`)
のみで、PDFファイルやpdfplumberには依存しない。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .classify.dimension import classify_dimension_lines
from .classify.frame import classify_drawing_frame
from .classify.grid import classify_grid_lines
from .classify.unread_info import classify_unread_info
from .classify.wall import classify_wall_lines
from .render import FillPolygon, _gradient_color, build_svg

_GRID_HIGHLIGHT_COLOR = "#ff4500"
_DIMENSION_HIGHLIGHT_COLOR = "#008000"
_WALL_SCORE_LOW_COLOR = (0x66, 0xCD, 0xAA)
_WALL_SCORE_HIGH_COLOR = (0xFF, 0x69, 0xB4)
_WALL_FILL_OPACITY = 0.5
_FRAME_HIGHLIGHT_COLOR = "#ff00ff"
_DOOR_COLOR = "#ffa500"
_DASHED_LOOP_COLOR = "#999999"
_HATCH_LOW_COLOR = (0xFF, 0x7F, 0x7F)
_HATCH_HIGH_COLOR = (0xBF, 0xFF, 0x7F)


def _load_payload(input_json: Path) -> dict:
    return json.loads(input_json.read_text(encoding="utf-8"))


def _write_json(output: Path, pretty: bool, serializable: list) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    indent = 2 if pretty else None
    output.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=indent, default=str),
        encoding="utf-8",
    )


def _write_svg(
    svg_path: Path,
    records: list,
    page_width: float,
    page_height: float,
    linewidth_scale: float,
    highlight_indices: set[int] | None = None,
    highlight_color: str = "#ff4500",
    fill_polygons: list[FillPolygon] | None = None,
    color_overrides: dict[int, str] | None = None,
) -> None:
    svg = build_svg(
        records,
        page_width,
        page_height,
        linewidth_scale=linewidth_scale,
        highlight_indices=highlight_indices,
        highlight_color=highlight_color,
        fill_polygons=fill_polygons,
        color_overrides=color_overrides,
    )
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")


def _parse_grid_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽出済みベクターデータから通り芯・通り芯番号を分類する"
    )
    parser.add_argument("input_json", type=Path, help="extract-vectorsが出力したJSON")
    parser.add_argument("-o", "--output", type=Path, default=None, help="分類結果JSONの出力先")
    parser.add_argument("--pretty", action="store_true", help="JSON出力をインデント付きで整形する")
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help=f"通り芯・通り芯番号を{_GRID_HIGHLIGHT_COLOR}で着色した再現SVGの出力先",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """`classify-grid-lines`のエントリポイント。"""
    args = _parse_grid_args(argv)

    payload = _load_payload(args.input_json)
    page_width = payload["page_width"]
    page_height = payload["page_height"]
    linewidth_scale = payload["linewidth_scale"]
    records = payload["records"]

    grid_lines = classify_grid_lines(records, page_width, page_height)

    print(f"detected {len(grid_lines)} grid line(s):")
    for grid_line in grid_lines:
        print(
            f"  label={grid_line.label.text!r} axis={grid_line.axis} "
            f"segments={len(grid_line.segment_indices)} span={grid_line.span} "
            f"sequence_plausible={grid_line.sequence_plausible}"
        )

    if args.output is not None:
        _write_json(args.output, args.pretty, [asdict(g) for g in grid_lines])
        print(f"wrote {len(grid_lines)} grid line(s) to {args.output}")

    if args.svg is not None:
        highlight_indices: set[int] = set()
        for grid_line in grid_lines:
            highlight_indices.add(grid_line.label.circle_index)
            highlight_indices.update(grid_line.label.char_indices)
            highlight_indices.update(grid_line.segment_indices)

        _write_svg(
            args.svg,
            records,
            page_width,
            page_height,
            linewidth_scale,
            highlight_indices,
            _GRID_HIGHLIGHT_COLOR,
        )
        print(f"wrote svg to {args.svg}")


def _parse_dimension_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽出済みベクターデータから寸法線・寸法を分類する"
    )
    parser.add_argument("input_json", type=Path, help="extract-vectorsが出力したJSON")
    parser.add_argument("-o", "--output", type=Path, default=None, help="分類結果JSONの出力先")
    parser.add_argument("--pretty", action="store_true", help="JSON出力をインデント付きで整形する")
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help=f"寸法線を{_DIMENSION_HIGHLIGHT_COLOR}で着色した再現SVGの出力先",
    )
    return parser.parse_args(argv)


def main_dimension(argv: list[str] | None = None) -> None:
    """`classify-dimension-lines`のエントリポイント。

    通り芯分類は内部で自動的に実行する(ユーザーが別途通り芯分類結果を
    用意する必要はない)。
    """
    args = _parse_dimension_args(argv)

    payload = _load_payload(args.input_json)
    page_width = payload["page_width"]
    page_height = payload["page_height"]
    linewidth_scale = payload["linewidth_scale"]
    records = payload["records"]

    grid_lines = classify_grid_lines(records, page_width, page_height)
    dimension_segments = classify_dimension_lines(records, grid_lines, page_width, page_height)

    print(f"detected {len(dimension_segments)} dimension segment(s):")
    for segment in dimension_segments:
        print(
            f"  value={segment.value_text!r} axis={segment.axis} "
            f"start={segment.start_point} end={segment.end_point} "
            f"is_axis_derived={segment.is_axis_derived}"
        )

    if args.output is not None:
        _write_json(args.output, args.pretty, [asdict(s) for s in dimension_segments])
        print(f"wrote {len(dimension_segments)} dimension segment(s) to {args.output}")

    if args.svg is not None:
        highlight_indices: set[int] = set()
        for segment in dimension_segments:
            highlight_indices.update(segment.value_char_indices)
            highlight_indices.update(segment.line_indices)
            if segment.start_marker_index is not None:
                highlight_indices.add(segment.start_marker_index)
            if segment.end_marker_index is not None:
                highlight_indices.add(segment.end_marker_index)
            highlight_indices.update(segment.start_extension_indices)
            highlight_indices.update(segment.end_extension_indices)

        _write_svg(
            args.svg,
            records,
            page_width,
            page_height,
            linewidth_scale,
            highlight_indices,
            _DIMENSION_HIGHLIGHT_COLOR,
        )
        print(f"wrote svg to {args.svg}")


def _parse_wall_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽出済みベクターデータから壁(内壁)をスコアリング方式で分類する"
    )
    parser.add_argument("input_json", type=Path, help="extract-vectorsが出力したJSON")
    parser.add_argument("-o", "--output", type=Path, default=None, help="分類結果JSONの出力先")
    parser.add_argument("--pretty", action="store_true", help="JSON出力をインデント付きで整形する")
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help=(
            f"壁候補をスコアに応じたグラデーション("
            f"低: rgb{_WALL_SCORE_LOW_COLOR} 〜 高: rgb{_WALL_SCORE_HIGH_COLOR}、"
            f"不透明度{_WALL_FILL_OPACITY})で塗りつぶした再現SVGの出力先"
        ),
    )
    return parser.parse_args(argv)


def main_wall(argv: list[str] | None = None) -> None:
    """`classify-wall-lines`のエントリポイント。

    通り芯分類・寸法線分類は内部で自動的に実行する(壁の判定条件の一つに
    寸法線との位置関係を使うため)。
    """
    args = _parse_wall_args(argv)

    payload = _load_payload(args.input_json)
    page_width = payload["page_width"]
    page_height = payload["page_height"]
    linewidth_scale = payload["linewidth_scale"]
    records = payload["records"]

    grid_lines = classify_grid_lines(records, page_width, page_height)
    dimension_segments = classify_dimension_lines(records, grid_lines, page_width, page_height)
    wall_runs = classify_wall_lines(records, dimension_segments, grid_lines, page_width, page_height)

    walls = [w for w in wall_runs if w.is_wall]
    print(f"detected {len(walls)} wall run(s) (of {len(wall_runs)} candidate(s)):")
    for wall in walls:
        print(
            f"  score={wall.score:.2f} segments={len(wall.segments)} "
            f"components={wall.score_components}"
        )

    if args.output is not None:
        _write_json(args.output, args.pretty, [asdict(w) for w in walls])
        print(f"wrote {len(walls)} wall run(s) to {args.output}")

    if args.svg is not None:
        scores = [w.score for w in walls]
        lo, hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
        fill_polygons = [
            FillPolygon(
                points=w.polygon,
                color=_gradient_color(
                    0.5 if hi == lo else (w.score - lo) / (hi - lo),
                    low_color=_WALL_SCORE_LOW_COLOR,
                    high_color=_WALL_SCORE_HIGH_COLOR,
                ),
                opacity=_WALL_FILL_OPACITY,
            )
            for w in walls
        ]
        _write_svg(
            args.svg,
            records,
            page_width,
            page_height,
            linewidth_scale,
            fill_polygons=fill_polygons,
        )
        print(f"wrote svg to {args.svg}")


def _parse_frame_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽出済みベクターデータから図面枠・図面メタ情報を分類する"
    )
    parser.add_argument("input_json", type=Path, help="extract-vectorsが出力したJSON")
    parser.add_argument("-o", "--output", type=Path, default=None, help="分類結果JSONの出力先")
    parser.add_argument("--pretty", action="store_true", help="JSON出力をインデント付きで整形する")
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help=f"図面枠・図面メタ情報を{_FRAME_HIGHLIGHT_COLOR}で着色した再現SVGの出力先",
    )
    return parser.parse_args(argv)


def main_frame(argv: list[str] | None = None) -> None:
    """`classify-drawing-frame`のエントリポイント。"""
    args = _parse_frame_args(argv)

    payload = _load_payload(args.input_json)
    page_width = payload["page_width"]
    page_height = payload["page_height"]
    linewidth_scale = payload["linewidth_scale"]
    records = payload["records"]

    frame = classify_drawing_frame(records, page_width, page_height)

    if frame is None:
        print("no drawing frame detected")
        return

    print(f"drawing frame bounds={frame.bounds}")
    print(f"  border lines: {frame.border_line_indices}")
    print(f"  attached lines: {frame.attached_line_indices}")
    print(f"  drawing area bounds={frame.drawing_bounds}")
    print(f"  meta area(s): {frame.meta_bounds}")
    print(f"  meta record count: {len(frame.meta_record_indices)}")

    if args.output is not None:
        _write_json(args.output, args.pretty, asdict(frame))
        print(f"wrote drawing frame to {args.output}")

    if args.svg is not None:
        highlight_indices: set[int] = set()
        highlight_indices.update(frame.border_line_indices)
        highlight_indices.update(frame.attached_line_indices)
        highlight_indices.update(frame.meta_record_indices)

        _write_svg(
            args.svg,
            records,
            page_width,
            page_height,
            linewidth_scale,
            highlight_indices,
            _FRAME_HIGHLIGHT_COLOR,
        )
        print(f"wrote svg to {args.svg}")


def _parse_unread_info_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽出済みベクターデータから読み取り不要な情報(点線閉ループ・ハッチング・扉)を分類する"
    )
    parser.add_argument("input_json", type=Path, help="extract-vectorsが出力したJSON")
    parser.add_argument("-o", "--output", type=Path, default=None, help="分類結果JSONの出力先")
    parser.add_argument("--pretty", action="store_true", help="JSON出力をインデント付きで整形する")
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help=(
            f"扉を{_DOOR_COLOR}、点線閉ループを{_DASHED_LOOP_COLOR}、"
            f"ハッチングをグループごとのグラデーション("
            f"rgb{_HATCH_LOW_COLOR}〜rgb{_HATCH_HIGH_COLOR})で着色した再現SVGの出力先"
        ),
    )
    return parser.parse_args(argv)


def main_unread_info(argv: list[str] | None = None) -> None:
    """`classify-unread-info`のエントリポイント。

    通り芯・寸法線・壁・図面枠の分類は内部で自動的に実行し、それらの
    分類結果に含まれるレコードは不要情報の検出対象から除外する。
    """
    args = _parse_unread_info_args(argv)

    payload = _load_payload(args.input_json)
    page_width = payload["page_width"]
    page_height = payload["page_height"]
    linewidth_scale = payload["linewidth_scale"]
    records = payload["records"]

    grid_lines = classify_grid_lines(records, page_width, page_height)
    dimension_segments = classify_dimension_lines(records, grid_lines, page_width, page_height)
    wall_runs = classify_wall_lines(records, dimension_segments, grid_lines, page_width, page_height)
    frame = classify_drawing_frame(records, page_width, page_height)

    excluded: set[int] = set()
    for grid_line in grid_lines:
        excluded.update(grid_line.segment_indices)
    for segment in dimension_segments:
        excluded.update(segment.line_indices)
        excluded.update(segment.start_extension_indices)
        excluded.update(segment.end_extension_indices)
    for wall_run in wall_runs:
        if wall_run.is_wall:
            excluded.update(wall_run.line_indices)
    if frame is not None:
        excluded.update(frame.border_line_indices)
        excluded.update(frame.attached_line_indices)
        excluded.update(frame.meta_record_indices)

    result = classify_unread_info(records, frozenset(excluded), page_width, page_height)

    print(f"detected {len(result.dashed_closed_loops)} dashed closed loop(s)")
    print(f"detected {len(result.doors)} door symbol(s)")
    print(f"detected {len(result.hatch_groups)} hatch group(s):")
    for group in result.hatch_groups:
        print(
            f"  group_id={group.group_id} linewidth={group.linewidth} gap={group.gap:.2f} "
            f"instances={len(group.instances)} lines={len(group.line_indices)}"
        )

    if args.output is not None:
        serializable = {
            "dashed_closed_loops": [asdict(loop) for loop in result.dashed_closed_loops],
            "doors": [asdict(door) for door in result.doors],
            "hatch_groups": [asdict(group) for group in result.hatch_groups],
        }
        _write_json(args.output, args.pretty, serializable)
        print(f"wrote unread info to {args.output}")

    if args.svg is not None:
        color_overrides: dict[int, str] = {}
        for loop in result.dashed_closed_loops:
            for index in loop.line_indices:
                color_overrides[index] = _DASHED_LOOP_COLOR
        for door in result.doors:
            color_overrides[door.arc_index] = _DOOR_COLOR

        hatch_groups = result.hatch_groups
        n_groups = len(hatch_groups)
        for i, group in enumerate(hatch_groups):
            ratio = i / (n_groups - 1) if n_groups > 1 else 0.0
            color = _gradient_color(ratio, low_color=_HATCH_LOW_COLOR, high_color=_HATCH_HIGH_COLOR)
            for index in group.line_indices:
                color_overrides[index] = color

        _write_svg(
            args.svg,
            records,
            page_width,
            page_height,
            linewidth_scale,
            color_overrides=color_overrides,
        )
        print(f"wrote svg to {args.svg}")


if __name__ == "__main__":
    main()
