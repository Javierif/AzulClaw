from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_framework import Message
from pydantic import BaseModel

from azul_backend.azul_brain.cortex.kernel_setup import _Result, _compose_instructions
from azul_backend.azul_brain.soul.system_prompt import AZULCLAW_SYSTEM_PROMPT
from azul_backend.azul_brain.runtime.agent_runtime import AgentRuntimeManager, _serialize_runtime_text
from azul_backend.azul_brain.runtime.store import RuntimeModelProfile


class StructuredValue(BaseModel):
    route: str


class ResultLike:
    def __init__(self, value):
        self.value = value


class StreamUpdate:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeStream:
    def __init__(self) -> None:
        self._updates = [StreamUpdate("hello "), StreamUpdate("world")]

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._updates):
            raise StopAsyncIteration
        update = self._updates[self._index]
        self._index += 1
        return update

    async def get_final_response(self):
        return ResultLike("final text")


class FakeStreamingAgent:
    def stream_messages(self, messages: list[Message]) -> FakeStream:
        return FakeStream()


class FakeProcessRegistry:
    def start(self, **kwargs):
        return SimpleNamespace(id="proc-1")

    def update(self, *args, **kwargs):
        return None

    def finish(self, *args, **kwargs):
        return None


class RuntimeSerializationTests(unittest.TestCase):
    def test_serialize_runtime_text_handles_structured_values(self) -> None:
        result = ResultLike(StructuredValue(route="none"))

        self.assertEqual(_serialize_runtime_text(result), '{"route":"none"}')

    def test_serialize_runtime_text_handles_dicts(self) -> None:
        result = ResultLike({"route": "none"})

        self.assertEqual(_serialize_runtime_text(result), '{"route": "none"}')

    def test_result_wrapper_stringifies_values(self) -> None:
        result = _Result({"route": "none"})

        self.assertEqual(result.text, '{"route": "none"}')
        self.assertEqual(str(result), '{"route": "none"}')

    def test_task_instructions_are_composed_with_base_prompt(self) -> None:
        instructions = _compose_instructions("Use no tools.")

        self.assertTrue(instructions.startswith(AZULCLAW_SYSTEM_PROMPT))
        self.assertIn("Task-specific instructions:\nUse no tools.", instructions)

    def test_empty_task_instructions_keep_base_prompt(self) -> None:
        self.assertEqual(_compose_instructions(""), AZULCLAW_SYSTEM_PROMPT)

    def test_serialize_runtime_text_strips_complete_thinking_blocks(self) -> None:
        result = ResultLike("<think>internal notes</think>Visible answer")

        self.assertEqual(_serialize_runtime_text(result), "Visible answer")

    def test_serialize_runtime_text_strips_dangling_closing_think_tag(self) -> None:
        result = ResultLike("internal greeting?</think>Hola. ¿En qué puedo ayudarte?")

        self.assertEqual(_serialize_runtime_text(result), "Hola. ¿En qué puedo ayudarte?")


    def test_serialize_runtime_text_preserves_literal_closing_think_tag_context(self) -> None:
        result = ResultLike("Use </think> in documentation when describing the tag.")

        self.assertEqual(
            _serialize_runtime_text(result),
            "Use </think> in documentation when describing the tag.",
        )


class RuntimeStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_result_initializes_value(self) -> None:
        manager = AgentRuntimeManager(
            mcp_client=None,
            store=None,
            process_registry=FakeProcessRegistry(),
        )
        model = RuntimeModelProfile(
            id="fast",
            label="Fast",
            lane="fast",
            provider="azure",
            deployment="fast-model",
            streaming_enabled=True,
        )
        manager._resolve_candidates = lambda lane: [model]  # type: ignore[method-assign]

        async def get_agent(*args, **kwargs):
            return FakeStreamingAgent()

        manager._get_agent = get_agent  # type: ignore[method-assign]
        deltas: list[str] = []

        async def on_delta(text: str) -> None:
            deltas.append(text)

        result = await manager.execute_messages_stream(
            messages=[],
            lane="fast",
            title="Test",
            source="test",
            kind="chat",
            on_delta=on_delta,
        )

        self.assertEqual(deltas, ["hello ", "world"])
        self.assertEqual(result.text, "final text")
        self.assertEqual(result.value, "final text")


if __name__ == "__main__":
    unittest.main()
