from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from agent_framework import Message
from pydantic import BaseModel

from azul_backend.azul_brain.cortex.kernel_setup import _Result, _compose_instructions, _normalize_dynamic_tool_name
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


class FakeCoroutineStreamingAgent:
    def stream_messages(self, messages: list[Message]):
        async def _make_stream() -> FakeStream:
            return FakeStream()
        return _make_stream()


class FakeProcessRegistry:
    def start(self, **kwargs):
        return SimpleNamespace(id="proc-1")

    def update(self, *args, **kwargs):
        return None

    def finish(self, *args, **kwargs):
        return None


class FakeSettingsStore:
    def __init__(self, models: list[RuntimeModelProfile], default_lane: str = "auto") -> None:
        self._settings = SimpleNamespace(default_lane=default_lane, models=models)

    def load_settings(self):
        return self._settings


class OverflowAgent:
    async def invoke_messages(self, messages: list[Message], response_format=None):
        raise RuntimeError(
            "The input (300000 tokens) is longer than the model's context length (262144 tokens)."
        )


class RecordingAgent:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    async def invoke_messages(self, messages: list[Message], response_format=None):
        self.calls.append(messages)
        return ResultLike(self.reply)


class RuntimeSerializationTests(unittest.TestCase):
    def test_dynamic_tool_names_are_bounded_for_long_skill_tools(self) -> None:
        used_names: set[str] = set()

        browse_name = _normalize_dynamic_tool_name(
            "dev.azulclaw.desktop-organizer",
            "list_target_folder_contents",
            used_names,
        )
        preview_name = _normalize_dynamic_tool_name(
            "dev.azulclaw.desktop-organizer",
            "preview_folder_organization",
            used_names,
        )

        self.assertLessEqual(len(browse_name), 64)
        self.assertLessEqual(len(preview_name), 64)
        self.assertNotEqual(browse_name, preview_name)
        self.assertRegex(browse_name, r"^[A-Za-z0-9_-]+$")
        self.assertRegex(preview_name, r"^[A-Za-z0-9_-]+$")

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

    def test_base_prompt_does_not_embed_marketplace_skill_contracts(self) -> None:
        self.assertNotIn("Folder Organizer", AZULCLAW_SYSTEM_PROMPT)

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

    async def test_streaming_result_supports_coroutine_wrapped_stream(self) -> None:
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
            return FakeCoroutineStreamingAgent()

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


class RuntimeRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_messages_preemptively_compacts_large_payloads(self) -> None:
        manager = AgentRuntimeManager(
            mcp_client=None,
            store=None,
            process_registry=FakeProcessRegistry(),
        )
        model = RuntimeModelProfile(
            id="slow",
            label="Slow",
            lane="slow",
            provider="azure",
            deployment="slow-model",
            streaming_enabled=False,
        )
        manager._resolve_candidates = lambda lane: [model]  # type: ignore[method-assign]

        recording_agent = RecordingAgent("preflight-recovered")
        agent_requests: list[dict[str, object]] = []

        async def get_agent(*args, **kwargs):
            agent_requests.append({"instructions": kwargs.get("instructions")})
            return recording_agent

        manager._get_agent = get_agent  # type: ignore[method-assign]
        messages = [
            Message(role="system", contents="A" * 200_000),
            Message(role="assistant", contents="history " * 10_000),
            Message(role="user", contents="Ordena también las subcarpetas."),
        ]

        result = await manager.execute_messages(
            messages=messages,
            lane="slow",
            title="Test",
            source="test",
            kind="chat",
        )

        self.assertEqual(result.text, "preflight-recovered")
        self.assertEqual(len(agent_requests), 1)
        self.assertIn("pre-compacted", str(agent_requests[0]["instructions"]).lower())
        self.assertEqual(len(recording_agent.calls), 1)
        sent_messages = recording_agent.calls[0]
        self.assertLess(len(sent_messages[0].contents[0].text), 200_000)
        self.assertEqual(sent_messages[-1].role, "user")

    async def test_execute_messages_retries_with_compacted_context_on_overflow(self) -> None:
        manager = AgentRuntimeManager(
            mcp_client=None,
            store=None,
            process_registry=FakeProcessRegistry(),
        )
        model = RuntimeModelProfile(
            id="slow",
            label="Slow",
            lane="slow",
            provider="azure",
            deployment="slow-model",
            streaming_enabled=False,
        )
        manager._resolve_candidates = lambda lane: [model]  # type: ignore[method-assign]

        overflow_agent = OverflowAgent()
        recovery_agent = RecordingAgent("recovered")
        agent_requests: list[dict[str, object]] = []

        async def get_agent(*args, **kwargs):
            agent_requests.append({"instructions": kwargs.get("instructions")})
            return overflow_agent if len(agent_requests) == 1 else recovery_agent

        manager._get_agent = get_agent  # type: ignore[method-assign]
        messages = [
            Message(role="system", contents="A" * 5_000),
            Message(role="assistant", contents="history " * 700),
            Message(role="assistant", contents="Document context for this conversation:\n" + ("memo " * 900)),
            Message(role="user", contents="Ordena también las subcarpetas."),
        ]

        result = await manager.execute_messages(
            messages=messages,
            lane="slow",
            title="Test",
            source="test",
            kind="chat",
        )

        self.assertEqual(result.text, "recovered")
        self.assertEqual(len(agent_requests), 2)
        self.assertIn("context limit", str(agent_requests[1]["instructions"]).lower())
        self.assertEqual(len(recovery_agent.calls), 1)
        retried_messages = recovery_agent.calls[0]
        first_text = retried_messages[0].contents[0].text
        self.assertLess(len(first_text), 5_000)
        self.assertEqual(retried_messages[-1].role, "user")


class RuntimeCandidateSelectionTests(unittest.TestCase):
    def test_inspect_candidate_skips_reports_fast_probe_failure(self) -> None:
        fast_model = RuntimeModelProfile(
            id="fast",
            label="Fast brain",
            lane="fast",
            provider="azure",
            deployment="fast-model",
        )
        slow_model = RuntimeModelProfile(
            id="slow",
            label="Slow brain",
            lane="slow",
            provider="azure",
            deployment="slow-model",
        )
        manager = AgentRuntimeManager(
            mcp_client=None,
            store=FakeSettingsStore([fast_model, slow_model]),
            process_registry=FakeProcessRegistry(),
        )

        manager._probe_status_for_model = lambda model: {  # type: ignore[method-assign]
            "available": model.id == "slow",
            "detail": "Azure configuration ready" if model.id == "slow" else "Fast endpoint missing",
        }

        candidates = manager._resolve_candidates("fast")
        skipped = manager._inspect_candidate_skips("fast", selected_model_ids={model.id for model in candidates})

        self.assertEqual([model.id for model in candidates], ["slow"])
        self.assertEqual(skipped[0]["model_id"], "fast")
        self.assertEqual(skipped[0]["reason"], "probe-unavailable")
        self.assertEqual(skipped[0]["reason_label"], "Availability probe failed")
        self.assertEqual(skipped[0]["detail"], "Fast endpoint missing")

    def test_inspect_candidate_skips_reports_fast_cooldown_with_previous_error(self) -> None:
        fast_model = RuntimeModelProfile(
            id="fast",
            label="Fast brain",
            lane="fast",
            provider="azure",
            deployment="fast-model",
        )
        slow_model = RuntimeModelProfile(
            id="slow",
            label="Slow brain",
            lane="slow",
            provider="azure",
            deployment="slow-model",
        )
        manager = AgentRuntimeManager(
            mcp_client=None,
            store=FakeSettingsStore([fast_model, slow_model]),
            process_registry=FakeProcessRegistry(),
        )
        manager.cooldowns["fast"] = 9999999999.0
        manager.last_errors["fast"] = "Fast deployment timed out"
        manager._probe_status_for_model = lambda model: {  # type: ignore[method-assign]
            "available": model.id == "slow",
            "detail": "Azure configuration ready",
        }

        candidates = manager._resolve_candidates("fast")
        skipped = manager._inspect_candidate_skips("fast", selected_model_ids={model.id for model in candidates})
        detail = json.loads(skipped[0]["detail"])

        self.assertEqual([model.id for model in candidates], ["slow"])
        self.assertEqual(skipped[0]["reason"], "cooldown")
        self.assertEqual(detail["last_error"], "Fast deployment timed out")
        self.assertTrue(detail["cooldown_until"])


if __name__ == "__main__":
    unittest.main()
