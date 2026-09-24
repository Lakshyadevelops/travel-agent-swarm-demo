"""Deterministic BaseLlm for benchmark mode.

Drives the identical agent graph and produces the identical scratchpad traffic as
the real model, at ~0ms latency. That is what makes storage differences
measurable: with real Gemini calls, model latency is 10^2-10^3x the storage
delta and swamps the signal entirely.

Each agent gets its own instance configured with the tool it should call, so
behaviour is a pure function of the agent role and turn number -- no parsing of
prompts, no nondeterminism.
"""

from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


class FakeLlm(BaseLlm):
    """Emits one tool call, then a short closing message."""

    role: str = "agent"
    tool_name: str | None = None
    closing: str = "Done."

    def __init__(self, *, role: str, tool_name: str | None, closing: str) -> None:
        super().__init__(model=f"fake/{role}")
        self.role = role
        self.tool_name = tool_name
        self.closing = closing

    @staticmethod
    def _already_called(llm_request: LlmRequest) -> bool:
        """True once a function response is present in the conversation."""
        for content in reversed(llm_request.contents or []):
            for part in content.parts or []:
                if getattr(part, "function_response", None) is not None:
                    return True
        return False

    @staticmethod
    def _usage() -> types.GenerateContentResponseUsageMetadata:
        """Zeroed usage, purely to keep ADK's token accounting from warning."""
        return types.GenerateContentResponseUsageMetadata(
            prompt_token_count=0, candidates_token_count=0, total_token_count=0
        )

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.tool_name and not self._already_called(llm_request):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=self.tool_name, args={}
                            )
                        )
                    ],
                ),
                usage_metadata=self._usage(),
            )
            return

        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=self.closing)]
            ),
            usage_metadata=self._usage(),
        )
