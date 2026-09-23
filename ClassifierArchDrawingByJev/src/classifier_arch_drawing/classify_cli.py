"""分類結果の検証用CLI(通り芯・通り芯番号の分類)。

抽出タスク(extract-vectors)とは独立に実行できる。入力は抽出タスクが
出力したJSON(`{"page_width","page_height","linewidth_scale","records"}`)
のみで、PDFファイルやpdfplumberには依存しない。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .classify.grid import classify_grid_lines
from .render import build_svg

_HIGHLIGHT_COLOR = "#ff4500"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽出済みベクターデータから通り芯・通り芯番号を分類する"
    )
    parser.add_argument("input_json", type=Path, help="extract-vectorsが出力したJSON")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="分類結果JSONの出力先"
    )
    parser.add_argument(
        "--pretty", action="store_true", help="JSON出力をインデント付きで整形する"
    )
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help=f"通り芯・通り芯番号を{_HIGHLIGHT_COLOR}で着色した再現SVGの出力先",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
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
        args.output.parent.mkdir(parents=True, exist_ok=True)
        indent = 2 if args.pretty else None
        serializable = [asdict(grid_line) for grid_line in grid_lines]
        args.output.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=indent, default=str),
            encoding="utf-8",
        )
        print(f"wrote {len(grid_lines)} grid line(s) to {args.output}")

    if args.svg is not None:
        highlight_indices: set[int] = set()
        for grid_line in grid_lines:
            highlight_indices.add(grid_line.label.circle_index)
            highlight_indices.update(grid_line.label.char_indices)
            highlight_indices.update(grid_line.segment_indices)

        svg = build_svg(
            records,
            page_width,
            page_height,
            linewidth_scale=linewidth_scale,
            highlight_indices=highlight_indices,
            highlight_color=_HIGHLIGHT_COLOR,
        )
        args.svg.parent.mkdir(parents=True, exist_ok=True)
        args.svg.write_text(svg, encoding="utf-8")
        print(f"wrote svg to {args.svg}")


if __name__ == "__main__":
    main()
