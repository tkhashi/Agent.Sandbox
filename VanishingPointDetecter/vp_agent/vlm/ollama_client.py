import base64
import json
import re
from pathlib import Path

import ollama

from .base import ToolCall, VLMClient, VLMResponse


class OllamaVLMClient(VLMClient):
    def __init__(self, model: str = "gemma3:4b", host: str = "http://localhost:11434"):
        self.model = model
        self.client = ollama.Client(host=host)

    def classify_and_act(
        self,
        image_path: str,
        messages: list[dict],
        tools: list[dict],
    ) -> VLMResponse:
        image_b64 = self._encode_image(image_path)
        ollama_messages = self._build_messages(messages, image_b64)

        raw = self.client.chat(
            model=self.model,
            messages=ollama_messages,
            tools=tools,
        )

        tool_calls = self._parse_tool_calls(raw)

        # テキスト回答 or 引数バリデーション失敗 → ツール使用を明示して1回リトライ
        if tool_calls is None or len(tool_calls) == 0:
            raw = self._retry_explicit_tool(ollama_messages, tools)
            tool_calls = self._parse_tool_calls(raw)

        # リトライ後もNone（JSON破損）→ 最終フォールバック
        if tool_calls is None:
            raw = self._retry_json(ollama_messages, tools)
            tool_calls = self._parse_tool_calls(raw) or []

        return VLMResponse(
            text=raw.message.content if raw.message.content else None,
            tool_calls=tool_calls,
            raw=raw,
        )

    def _encode_image(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_messages(self, messages: list[dict], image_b64: str) -> list[dict]:
        result = []
        for i, msg in enumerate(messages):
            if i == 0 and msg["role"] == "user":
                result.append({
                    "role": "user",
                    "content": msg["content"],
                    "images": [image_b64],
                })
            else:
                result.append(msg)
        return result

    def _parse_tool_calls(self, raw) -> list[ToolCall] | None:
        if not hasattr(raw, "message") or not raw.message:
            return None

        msg = raw.message

        if hasattr(msg, "tool_calls") and msg.tool_calls:
            calls = []
            for tc in msg.tool_calls:
                try:
                    name = tc.function.name
                    args = tc.function.arguments
                    if isinstance(args, str):
                        args = json.loads(args)
                    if not self._validate_tool_args(name, args):
                        return None
                    calls.append(ToolCall(name=name, arguments=args))
                except (json.JSONDecodeError, AttributeError):
                    return None
            return calls

        # tool_calls未設定でもcontentにJSON埋め込まれる場合のフォールバック
        if msg.content:
            return self._extract_json_tool_call(msg.content)

        return []

    _REQUIRED_ARGS: dict[str, list[str]] = {
        "classify_perspective": ["perspective_type", "confidence", "scores", "reasoning"],
        "find_vanishing_points": ["num_vanishing_points"],
    }
    _VALID_ENUMS: dict[str, dict[str, list]] = {
        "classify_perspective": {
            "perspective_type": ["one_point", "two_point", "three_point", "none"],
            "confidence": ["high", "low"],
        },
        "find_vanishing_points": {
            "num_vanishing_points": [1, 2, 3],
        },
    }
    _SCORE_KEYS = ["one_point", "two_point", "three_point", "none"]

    def _validate_tool_args(self, name: str, args: dict) -> bool:
        required = self._REQUIRED_ARGS.get(name, [])
        for field in required:
            if field not in args:
                return False
        enums = self._VALID_ENUMS.get(name, {})
        for field, valid in enums.items():
            if field in args and args[field] not in valid:
                return False
        if name == "classify_perspective" and "scores" in args:
            s = args["scores"]
            if not isinstance(s, dict):
                return False
            for key in self._SCORE_KEYS:
                if key not in s or not isinstance(s[key], int):
                    return False
        return True

    def _extract_json_tool_call(self, content: str) -> list[ToolCall] | None:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group())
            if "name" in data and "arguments" in data:
                return [ToolCall(name=data["name"], arguments=data["arguments"])]
        except json.JSONDecodeError:
            return None
        return []

    def _retry_explicit_tool(self, messages: list[dict], tools: list[dict]):
        tool_names = [t["name"] for t in tools]
        names_str = " / ".join(tool_names)
        retry_messages = messages + [{
            "role": "user",
            "content": f"必ず以下のツールを呼び出してください: {names_str}。テキストで回答せず、ツール呼び出しのみで応答してください。",
        }]
        return self.client.chat(
            model=self.model,
            messages=retry_messages,
            tools=tools,
        )

    def _retry_json(self, messages: list[dict], tools: list[dict]):
        retry_messages = messages + [{
            "role": "user",
            "content": "JSON形式で再出力してください。",
        }]
        return self.client.chat(
            model=self.model,
            messages=retry_messages,
            tools=tools,
        )
