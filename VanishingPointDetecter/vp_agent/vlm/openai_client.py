import base64
import os

from openai import OpenAI

from .base import ToolCall, VLMClient, VLMResponse


class OpenAIVLMClient(VLMClient):
    def __init__(self, model: str = "gpt-4o-mini", api_key_env: str = "OPENAI_API_KEY"):
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"環境変数 {api_key_env} が未設定")
        self.model = model
        self.client = OpenAI(api_key=api_key)

    def classify_and_act(
        self,
        image_path: str,
        messages: list[dict],
        tools: list[dict],
    ) -> VLMResponse:
        openai_messages = self._build_messages(messages, image_path)
        openai_tools = self._convert_tools(tools)

        raw = self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=openai_tools,
        )

        choice = raw.choices[0]
        tool_calls = []
        if choice.message.tool_calls:
            import json
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return VLMResponse(
            text=choice.message.content,
            tool_calls=tool_calls,
            raw=raw,
        )

    def _encode_image(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_messages(self, messages: list[dict], image_path: str) -> list[dict]:
        result = []
        image_b64 = self._encode_image(image_path)
        suffix = image_path.rsplit(".", 1)[-1].lower()
        media_type = f"image/{suffix}" if suffix in ("png", "gif", "webp") else "image/jpeg"

        for i, msg in enumerate(messages):
            if i == 0 and msg["role"] == "user":
                result.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": msg["content"]},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                        },
                    ],
                })
            else:
                result.append(msg)
        return result

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        return [
            {"type": "function", "function": t}
            for t in tools
        ]
