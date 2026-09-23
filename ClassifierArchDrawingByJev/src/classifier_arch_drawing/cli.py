"""ベクター抽出・検証用CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pdfplumber

from .calibration import CalibrationResult, calibrate_linewidth
from .extract import extract_document_vectors, extract_page_vectors, summarize
from .render import build_svg


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="建築図面PDFからベクター情報(線・矩形・曲線・文字)を抽出する"
    )
    parser.add_argument("pdf_path", type=Path, help="入力PDFファイル")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="中間データJSONの出力先"
    )
    parser.add_argument(
        "--pretty", action="store_true", help="JSON出力をインデント付きで整形する"
    )
    parser.add_argument(
        "--svg",
        type=Path,
        default=None,
        help="検出した線(line/rect/curve)をそのまま再描画したSVGの出力先",
    )
    parser.add_argument(
        "--linewidth-scale",
        type=float,
        default=None,
        help="linewidth補正係数を手動指定する(自動キャリブレーションを無効化)",
    )
    parser.add_argument(
        "--no-linewidth-calibration",
        action="store_true",
        help="linewidthの自動キャリブレーションを行わず係数1.0を使用する",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    records = extract_document_vectors(args.pdf_path)
    stats = summarize(records)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))

    need_calibration = args.output is not None or args.svg is not None
    calib: CalibrationResult | None = None
    page_width = page_height = None

    if need_calibration:
        with pdfplumber.open(args.pdf_path) as pdf:
            page = pdf.pages[0]
            page_width, page_height = page.width, page.height

            if args.linewidth_scale is not None:
                calib = CalibrationResult(
                    scale=args.linewidth_scale, sample_count=0, candidate_count=0, method="manual"
                )
            elif args.no_linewidth_calibration:
                calib = CalibrationResult(
                    scale=1.0, sample_count=0, candidate_count=0, method="fallback_disabled"
                )
            else:
                calib = calibrate_linewidth(page, extract_page_vectors(page))

            print(
                f"linewidth calibration: scale={calib.scale:.4f} "
                f"(method={calib.method}, samples={calib.sample_count}/{calib.candidate_count}, "
                f"confidence={calib.confidence:.2f})"
            )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        indent = 2 if args.pretty else None
        payload = {
            "page_width": page_width,
            "page_height": page_height,
            "linewidth_scale": calib.scale,
            "records": records,
        }
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=indent, default=str),
            encoding="utf-8",
        )
        print(f"wrote {len(records)} records to {args.output}")

    if args.svg is not None:
        svg = build_svg(records, page_width, page_height, linewidth_scale=calib.scale)
        args.svg.parent.mkdir(parents=True, exist_ok=True)
        args.svg.write_text(svg, encoding="utf-8")
        print(f"wrote svg to {args.svg}")


if __name__ == "__main__":
    main()
