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

import asyncio
import random
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.llm.latency import CURRENT_LLM_LATENCY


class FakeLlm(BaseLlm):
    """Emits one tool call, then a short closing message.

    With a latency model bound (CURRENT_LLM_LATENCY), each response is delayed
    by recorded real-model latency for the same agent role -- see latency.py.
    """

    role: str = "agent"
    tool_name: str | None = None
    closing: str = "Done."
    _pending_final_ms: float | None = None
    _invocations: int = 0

    def __init__(self, *, role: str, tool_name: str | None, closing: str) -> None:
        super().__init__(model=f"fake/{role}")
        self.role = role
        self.tool_name = tool_name
        self.closing = closing

    @staticmethod
    def _already_called(llm_request: LlmRequest) -> bool:
        """True when the latest turn is our tool's response (i.e. mid-invocation).

        Only the last content counts: in a LoopAgent the agent's own response
        from an earlier round is still in history, and a real model re-plans
        after the guardrail's feedback rather than treating the job as done.
        """
        contents = llm_request.contents or []
        if not contents:
            return False
        return any(
            getattr(part, "function_response", None) is not None
            for part in contents[-1].parts or []
        )

    @staticmethod
    def _usage() -> types.GenerateContentResponseUsageMetadata:
        """Zeroed usage, purely to keep ADK's token accounting from warning."""
        return types.GenerateContentResponseUsageMetadata(
            prompt_token_count=0, candidates_token_count=0, total_token_count=0
        )

    async def _think(self, will_call_tool: bool) -> None:
        """Replay recorded model latency, if a latency model is bound."""
        bound = CURRENT_LLM_LATENCY.get()
        if bound is None:
            return
        model, session_seed = bound
        if will_call_tool or self.tool_name is None:
            # One RNG per (session, agent, invocation): ParallelAgent scheduling
            # order must not change which agent gets which sample, or arms stop
            # replaying identical workloads.
            self._invocations += 1
            rng = random.Random(f"{session_seed}:{self.role}:{self._invocations}")
            tool_ms, final_ms = model.sample(rng, self.role)
            self._pending_final_ms = final_ms
            delay = tool_ms if will_call_tool else tool_ms + final_ms
        else:
            delay = self._pending_final_ms or 0.0
            self._pending_final_ms = None
        if delay > 0:
            await asyncio.sleep(delay / 1000.0)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.tool_name and not self._already_called(llm_request):
            await self._think(will_call_tool=True)
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

        await self._think(will_call_tool=False)
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=self.closing)]
            ),
            usage_metadata=self._usage(),
        )
