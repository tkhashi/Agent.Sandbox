import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from cv.toolkit import CVToolkit
from tools_schema import TOOLS
from validators import is_too_few_lines, is_valid_vp_result
from vlm.base import VLMClient, VLMResponse


def build_vlm_client(config: dict) -> VLMClient:
    provider = config["vlm"]["provider"]
    if provider == "ollama":
        from vlm.ollama_client import OllamaVLMClient
        cfg = config["vlm"]["ollama"]
        return OllamaVLMClient(model=cfg["model"], host=cfg["host"])
    elif provider == "openai":
        from vlm.openai_client import OpenAIVLMClient
        cfg = config["vlm"]["openai"]
        return OpenAIVLMClient(model=cfg["model"], api_key_env=cfg["api_key_env"])
    raise ValueError(f"unknown provider: {provider}")


def dispatch_tool(call, cv: CVToolkit, state: dict, image_path: str) -> str:
    name = call.name
    args = call.arguments

    if name == "classify_perspective":
        state["perspective_type"] = args["perspective_type"]
        state["confidence"] = args["confidence"]
        state["scores"] = args.get("scores")
        state["reasoning"] = args.get("reasoning", "")
        return json.dumps({
            "status": "ok",
            "perspective_type": args["perspective_type"],
            "confidence": args["confidence"],
            "scores": args.get("scores"),
            "reasoning": args.get("reasoning", ""),
        })

    elif name == "detect_lines":
        canny_low = args.get("canny_low", 50)
        canny_high = args.get("canny_high", 150)
        lines = cv.detect_lines(image_path, canny_low=canny_low, canny_high=canny_high)
        state["lines"] = lines
        state["canny_low"] = canny_low
        state["canny_high"] = canny_high
        return json.dumps({"status": "ok", "num_lines": len(lines)})

    elif name == "find_vanishing_points":
        if state.get("lines") is None:
            return json.dumps({"status": "error", "reason": "detect_lines を先に実行"})
        num_vps = args["num_vanishing_points"]
        vps = cv.find_vanishing_points(state["lines"], num_vps)
        state["vps"] = vps
        return json.dumps({
            "status": "ok",
            "vanishing_points": [
                {"x": vp.x, "y": vp.y, "support_lines": vp.support_lines, "confidence": vp.confidence}
                for vp in vps
            ],
        })

    elif name == "retry_with_adjusted_params":
        canny_low = args.get("canny_low", 30)
        canny_high = args.get("canny_high", 100)
        lines = cv.detect_lines(image_path, canny_low=canny_low, canny_high=canny_high)
        state["lines"] = lines
        state["canny_low"] = canny_low
        state["canny_high"] = canny_high
        return json.dumps({"status": "ok", "num_lines": len(lines)})

    return json.dumps({"status": "error", "reason": f"unknown tool: {name}"})


_VP_COUNT = {"one_point": 1, "two_point": 2, "three_point": 3}


def _select_tools(state: dict) -> list[dict]:
    if state.get("perspective_type") is None:
        return [t for t in TOOLS if t["name"] == "classify_perspective"]
    elif state["perspective_type"] == "none":
        return []
    elif state.get("lines") is None:
        return [t for t in TOOLS if t["name"] == "detect_lines"]
    elif state.get("vps") is None:
        return [t for t in TOOLS if t["name"] == "find_vanishing_points"]
    else:
        return [t for t in TOOLS if t["name"] == "retry_with_adjusted_params"]


def _make_step_prompt(state: dict) -> str:
    if state.get("perspective_type") is None:
        return (
            "Classify the perspective type of this image. "
            "Call classify_perspective with: "
            "perspective_type (one_point/two_point/three_point/none), "
            "confidence (high/low), "
            "scores (integer 0-100 for EACH of one_point/two_point/three_point/none independently — do NOT sum to 100), "
            "reasoning (explain visual evidence for chosen type and why others were ruled out)."
        )
    elif state.get("lines") is None:
        pt = state["perspective_type"]
        return f"Perspective classified as {pt}. Now call detect_lines to extract line segments from the image."
    elif state.get("vps") is None:
        num = _VP_COUNT.get(state["perspective_type"], 1)
        return f"Lines detected. Now call find_vanishing_points with num_vanishing_points={num}."
    else:
        return "Detection quality was insufficient. Call retry_with_adjusted_params with lower canny_low and canny_high values."


def _save_intermediate(
    cv: CVToolkit,
    state: dict,
    image_path: str,
    out_dir: Path,
    stage: str,
) -> None:
    try:
        if stage == "lines":
            canny_low = state.get("canny_low", 50)
            canny_high = state.get("canny_high", 150)
            cv.draw_edges(image_path, canny_low, canny_high).save(out_dir / "01_edges.jpg")
            cv.draw_lines_on_image(image_path, state["lines"]).save(out_dir / "02_lines.jpg")

        elif stage == "vps":
            pt = state.get("perspective_type", "one_point")
            num = _VP_COUNT.get(pt, 1)
            cv.draw_clusters(image_path, state["lines"], num).save(out_dir / "03_clusters.jpg")
            from cv.toolkit import VanishingPoint
            vps = state.get("vps") or []
            cv.draw_overlay(image_path, vps).save(out_dir / "04_vanishing_points.jpg")
    except Exception as e:
        print(f"中間画像保存失敗 ({stage}): {e}", file=sys.stderr)


def run_agent(
    image_path: str,
    vlm: VLMClient,
    cv: CVToolkit,
    config: dict,
    max_steps: int = 8,
    output_dir: Path | None = None,
) -> dict:
    img = cv2.imread(image_path)
    if img is None:
        return {"status": "error", "reason": f"画像読み込み失敗: {image_path}"}
    h, w = img.shape[:2]

    state: dict = {
        "perspective_type": None,
        "confidence": None,
        "scores": None,
        "reasoning": None,
        "lines": None,
        "vps": None,
        "attempts": 0,
    }

    cv_cfg = config.get("cv", {})
    min_lines = cv_cfg.get("min_lines_threshold", 5)
    min_cluster_lines = cv_cfg.get("min_cluster_lines", 3)
    max_vp_ratio = cv_cfg.get("max_vp_distance_ratio", 50.0)
    max_retries = config.get("agent", {}).get("max_retry_attempts", 2)

    messages: list[dict] = []

    for step in range(max_steps):
        current_tools = _select_tools(state)

        if not current_tools:
            break

        prompt = _make_step_prompt(state)
        messages.append({"role": "user", "content": prompt})

        response: VLMResponse = vlm.classify_and_act(image_path, messages, current_tools)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            if call.name == "detect_lines":
                result_str = dispatch_tool(call, cv, state, image_path)
                if is_too_few_lines(state["lines"] or [], min_lines):
                    adjusted_low = max(10, (call.arguments.get("canny_low", 50) - 20))
                    adjusted_high = max(50, (call.arguments.get("canny_high", 150) - 50))
                    lines = cv.detect_lines(image_path, canny_low=adjusted_low, canny_high=adjusted_high)
                    state["lines"] = lines
                    result_str = json.dumps({"status": "ok", "num_lines": len(lines), "auto_adjusted": True})
                messages.append({"role": "tool", "name": call.name, "content": result_str})
                if output_dir and state["lines"]:
                    _save_intermediate(cv, state, image_path, output_dir, "lines")
                continue

            result_str = dispatch_tool(call, cv, state, image_path)
            messages.append({"role": "tool", "name": call.name, "content": result_str})

            if call.name == "find_vanishing_points":
                if output_dir and state.get("vps") is not None:
                    _save_intermediate(cv, state, image_path, output_dir, "vps")
                vps = state.get("vps") or []
                if not is_valid_vp_result(vps, w, h, min_cluster_lines, max_vp_ratio):
                    state["attempts"] += 1
                    if state["attempts"] >= max_retries:
                        return {"status": "failed", "reason": "検出失敗（再試行上限）"}
                    state["vps"] = None
                    state["lines"] = None

            if call.name == "retry_with_adjusted_params":
                state["vps"] = None

        if (state["perspective_type"] is not None
                and state["lines"] is not None
                and state["vps"] is not None):
            vps = state["vps"]
            if is_valid_vp_result(vps, w, h, min_cluster_lines, max_vp_ratio):
                break

    result = {
        "status": "success",
        "perspective_type": state.get("perspective_type"),
        "confidence": state.get("confidence"),
        "scores": state.get("scores"),
        "reasoning": state.get("reasoning"),
        "vanishing_points": [
            {"x": vp.x, "y": vp.y, "support_lines": vp.support_lines}
            for vp in (state.get("vps") or [])
        ],
        "output_dir": str(output_dir) if output_dir else None,
    }

    if output_dir:
        try:
            (output_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            print(f"result.json 保存失敗: {e}", file=sys.stderr)

    return result


def main():
    parser = argparse.ArgumentParser(description="消失点検出Agent")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")
    parser.add_argument("--output-dir", help="成果物保存先ディレクトリ（タイムスタンプサブディレクトリが作成される）")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"設定ファイルが見つからない: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    output_dir: Path | None = None
    if args.output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir) / ts
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.image, output_dir / "00_input.jpg")
        print(f"出力先: {output_dir}", file=sys.stderr)

    vlm = build_vlm_client(config)
    cv_toolkit = CVToolkit()

    result = run_agent(
        image_path=args.image,
        vlm=vlm,
        cv=cv_toolkit,
        config=config,
        max_steps=config.get("agent", {}).get("max_steps", 8),
        output_dir=output_dir,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
