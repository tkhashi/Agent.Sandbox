from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class VLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class VLMClient(ABC):
    @abstractmethod
    def classify_and_act(
        self,
        image_path: str,
        messages: list[dict],
        tools: list[dict],
    ) -> VLMResponse:
        """画像+会話履歴+ツール定義を渡し、モデルの判断結果を返す"""
        ...
