from __future__ import annotations

import json
import shutil
import sys
import types
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from azul_backend.azul_brain.runtime.heartbeat_intent import (
    FREQUENCY_CLARIFICATION,
    HeartbeatDraft,
    HeartbeatDraftModel,
    HeartbeatIntentService,
    HeartbeatRouteModel,
    PendingHeartbeatStore,
)
from azul_backend.azul_brain.runtime.store import RuntimeStore
from azul_backend.azul_brain.runtime.store import to_iso_z, utc_now


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "memory" / "test-heartbeat-intent"


@contextmanager
def temp_runtime_dir() -> Iterator[Path]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    root = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def make_store(root: Path) -> RuntimeStore:
    return RuntimeStore(
        settings_path=root / "runtime_settings.json",
        jobs_path=root / "runtime_jobs.json",
        process_history_path=root / "runtime_process_history.json",
    )


@contextmanager
def fake_agent_framework_module() -> Iterator[None]:
    fake_agent_framework = types.ModuleType("agent_framework")

    class FakeMessage:
        def __init__(self, role: str, contents: str):
            self.role = role
            self.contents = contents

    fake_agent_framework.Message = FakeMessage
    original_agent_framework = sys.modules.get("agent_framework")
    sys.modules["agent_framework"] = fake_agent_framework
    try:
        yield
    finally:
        if original_agent_framework is None:
            sys.modules.pop("agent_framework", None)
        else:
            sys.modules["agent_framework"] = original_agent_framework


class FakeRuntimeResult:
    def __init__(self, value: HeartbeatRouteModel | None):
        self.value = value
        self.text = value.model_dump_json() if value is not None else ""


class FakeRuntimeManager:
    def __init__(self, store: RuntimeStore, value: HeartbeatRouteModel | None = None):
        self.store = store
        self.value = value
        self.calls = 0
        self.last_kwargs: dict | None = None

    async def execute_messages(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return FakeRuntimeResult(self.value)


class FailingRuntimeManager(FakeRuntimeManager):
    async def execute_messages(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        raise RuntimeError("fast model unavailable")


def create_route(*, name: str, prompt: str, cron_expression: str) -> HeartbeatRouteModel:
    return HeartbeatRouteModel(
        route="create_heartbeat",
        draft=HeartbeatDraftModel(
            name=name,
            prompt=prompt,
            cron_expression=cron_expression,
            lane="fast",
        ),
    )


class HeartbeatIntentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_create_route_creates_pending_heartbeat(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FakeRuntimeManager(
                store,
                value=create_route(
                    name="Inbox triage",
                    prompt="Review Inbox and flag urgent items.",
                    cron_expression="*/30 * * * *",
                ),
            )
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=PendingHeartbeatStore(root / "pending.json"),
            )
            service._requires_confirmation = lambda: True

            with fake_agent_framework_module():
                outcome = await service.handle_message(
                    "desktop-user",
                    "Every half hour, review my Inbox.",
                )

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIsNotNone(outcome.pending)
            self.assertIn("I can create this heartbeat", outcome.response)
            self.assertIn("`*/30 * * * *`", outcome.response)
            self.assertEqual(store.load_jobs(), [])
            self.assertEqual(runtime.calls, 1)
            self.assertIs(runtime.last_kwargs["response_format"], HeartbeatRouteModel)
            self.assertFalse(runtime.last_kwargs["tools_enabled"])
            self.assertIsNone(runtime.last_kwargs["instructions"])
            pending = json.loads((root / "pending.json").read_text(encoding="utf-8"))
            self.assertEqual(pending[0]["draft"]["cron_expression"], "*/30 * * * *")
            self.assertEqual(pending[0]["draft"]["prompt"], "Review Inbox and flag urgent items.")

    async def test_confirmation_creates_cron_job_and_clears_pending_action(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FakeRuntimeManager(
                store,
                value=create_route(
                    name="Heartbeat file review",
                    prompt="Review HEARTBEAT.md.",
                    cron_expression="*/30 * * * *",
                ),
            )
            pending_store = PendingHeartbeatStore(root / "pending.json")
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=pending_store,
            )
            service._requires_confirmation = lambda: True

            with fake_agent_framework_module():
                await service.handle_message(
                    "desktop-user",
                    "Every 30 minutes, review HEARTBEAT.md.",
                )

            runtime.value = HeartbeatRouteModel(route="confirm_pending")
            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "yes, create it")

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIsNotNone(outcome.job)
            jobs = store.load_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertFalse(jobs[0].system)
            self.assertEqual(jobs[0].schedule_kind, "cron")
            self.assertEqual(jobs[0].cron_expression, "*/30 * * * *")
            self.assertTrue(jobs[0].next_run_at)
            self.assertEqual(pending_store.load(), [])
            self.assertIn("I created the heartbeat", outcome.response)

    async def test_failed_confirmation_preserves_pending_action(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            pending_store = PendingHeartbeatStore(root / "pending.json")
            pending_store.save_for_user(
                "desktop-user",
                HeartbeatDraft(
                    name="Too frequent",
                    prompt="Run too frequently.",
                    cron_expression="*/10 * * * * *",
                    lane="fast",
                ),
            )
            runtime = FakeRuntimeManager(store, value=HeartbeatRouteModel(route="confirm_pending"))
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=pending_store,
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "yes, create it")

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIn("I could not create the heartbeat", outcome.response)
            self.assertIsNotNone(outcome.pending)
            pending = pending_store.get_for_user("desktop-user")
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.draft["cron_expression"], "*/10 * * * * *")
            self.assertEqual(store.load_jobs(), [])

    async def test_immediate_creation_error_returns_clarification(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FakeRuntimeManager(
                store,
                value=create_route(
                    name="Too frequent",
                    prompt="Run too frequently.",
                    cron_expression="*/10 * * * * *",
                ),
            )
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=PendingHeartbeatStore(root / "pending.json"),
            )
            service._requires_confirmation = lambda: False

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "Run this every 10 seconds")

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIn("I could not create the heartbeat", outcome.response)
            self.assertIsNone(outcome.pending)
            self.assertEqual(store.load_jobs(), [])

    async def test_request_creates_cron_job_immediately_when_confirmation_is_disabled(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FakeRuntimeManager(
                store,
                value=create_route(
                    name="Inbox review",
                    prompt="Review my Inbox.",
                    cron_expression="0 * * * *",
                ),
            )
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=PendingHeartbeatStore(root / "pending.json"),
            )
            service._requires_confirmation = lambda: False

            with fake_agent_framework_module():
                outcome = await service.handle_message(
                    "desktop-user",
                    "Every hour, review my Inbox.",
                )

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIsNotNone(outcome.job)
            job = store.load_jobs()[0]
            self.assertEqual(job.schedule_kind, "cron")
            self.assertEqual(job.cron_expression, "0 * * * *")
            self.assertFalse((root / "pending.json").exists())

    async def test_incomplete_create_route_asks_human_for_frequency(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FakeRuntimeManager(
                store,
                value=HeartbeatRouteModel(
                    route="create_heartbeat",
                    draft=HeartbeatDraftModel(
                        name="Inbox review",
                        prompt="Review my Inbox.",
                        cron_expression="",
                    ),
                ),
            )
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=PendingHeartbeatStore(root / "pending.json"),
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "Review my Inbox regularly.")

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertEqual(outcome.response, FREQUENCY_CLARIFICATION)
            self.assertEqual(store.load_jobs(), [])

    async def test_non_heartbeat_message_is_ignored_after_semantic_route(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FakeRuntimeManager(store, value=HeartbeatRouteModel(route="none"))
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=PendingHeartbeatStore(root / "pending.json"),
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "explain what a heartbeat is")

            self.assertIsNone(outcome)
            self.assertEqual(runtime.calls, 1)

    async def test_semantic_routing_failure_does_not_hijack_normal_chat(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            runtime = FailingRuntimeManager(store)
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=PendingHeartbeatStore(root / "pending.json"),
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "hello")

            self.assertIsNone(outcome)
            self.assertEqual(runtime.calls, 1)

    async def test_semantic_routing_failure_does_not_hijack_chat_with_pending_confirmation(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            pending_store = PendingHeartbeatStore(root / "pending.json")
            pending_store.save_for_user(
                "desktop-user",
                HeartbeatDraft(
                    name="Water reminder",
                    prompt="Remind me to drink water.",
                    cron_expression="0 * * * *",
                    lane="fast",
                ),
            )
            runtime = FailingRuntimeManager(store)
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=pending_store,
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "estas vivo")

            self.assertIsNone(outcome)
            self.assertIsNotNone(pending_store.get_for_user("desktop-user"))
            self.assertEqual(store.load_jobs(), [])

    async def test_semantic_routing_failure_still_accepts_explicit_local_confirmation(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            pending_store = PendingHeartbeatStore(root / "pending.json")
            pending_store.save_for_user(
                "desktop-user",
                HeartbeatDraft(
                    name="Water reminder",
                    prompt="Remind me to drink water.",
                    cron_expression="0 * * * *",
                    lane="fast",
                ),
            )
            runtime = FailingRuntimeManager(store)
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=pending_store,
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "yes")

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIn("I created the heartbeat", outcome.response)
            self.assertEqual(pending_store.load(), [])
            self.assertEqual(len(store.load_jobs()), 1)

    async def test_pending_confirmation_expires_after_ttl(self) -> None:
        with temp_runtime_dir() as root:
            store = make_store(root)
            pending_path = root / "pending.json"
            pending_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "pending-heartbeat-create",
                            "user_id": "desktop-user",
                            "draft": {
                                "name": "Expired reminder",
                                "prompt": "Remind me to drink water.",
                                "cron_expression": "0 * * * *",
                                "lane": "fast",
                            },
                            "created_at": to_iso_z(utc_now() - timedelta(minutes=11)),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            pending_store = PendingHeartbeatStore(pending_path)
            runtime = FailingRuntimeManager(store)
            service = HeartbeatIntentService(
                runtime_manager=runtime,
                store=store,
                pending_store=pending_store,
            )

            with fake_agent_framework_module():
                outcome = await service.handle_message("desktop-user", "yes")

            self.assertIsNone(outcome)
            self.assertEqual(pending_store.load(), [])
            self.assertEqual(store.load_jobs(), [])


if __name__ == "__main__":
    unittest.main()
