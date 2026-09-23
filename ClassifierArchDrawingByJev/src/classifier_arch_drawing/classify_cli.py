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
from .classify.grid import classify_grid_lines
from .render import build_svg

_GRID_HIGHLIGHT_COLOR = "#ff4500"
_DIMENSION_HIGHLIGHT_COLOR = "#008000"


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
    highlight_indices: set[int],
    highlight_color: str,
) -> None:
    svg = build_svg(
        records,
        page_width,
        page_height,
        linewidth_scale=linewidth_scale,
        highlight_indices=highlight_indices,
        highlight_color=highlight_color,
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


if __name__ == "__main__":
    main()
