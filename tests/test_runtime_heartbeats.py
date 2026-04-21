from __future__ import annotations

import json
import shutil
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

from azul_backend.azul_brain.runtime.scheduler import RuntimeScheduler
from azul_backend.azul_brain.memory.safe_memory import SafeMemory
from azul_backend.azul_brain.runtime.store import (
    SYSTEM_HEARTBEAT_DEFAULT_PROMPT,
    SYSTEM_HEARTBEAT_JOB_ID,
    RuntimeStore,
    parse_iso_datetime,
    to_iso_z,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "memory" / "test-runtime-heartbeats"


@contextmanager
def temp_runtime_dir() -> Iterator[str]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    root = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def make_store(root: Path) -> RuntimeStore:
    return RuntimeStore(
        settings_path=root / "runtime_settings.json",
        jobs_path=root / "runtime_jobs.json",
        process_history_path=root / "runtime_process_history.json",
    )


class RuntimeStoreHeartbeatTests(unittest.TestCase):
    def test_ensure_creates_system_heartbeat_with_next_run(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))

            job = store.ensure_system_heartbeat_job()

            self.assertEqual(job.id, SYSTEM_HEARTBEAT_JOB_ID)
            self.assertTrue(job.system)
            self.assertEqual(job.source, "system")
            self.assertEqual(job.schedule_kind, "every")
            self.assertGreaterEqual(job.interval_seconds, 60)
            self.assertIsNotNone(parse_iso_datetime(job.next_run_at))

    def test_ensure_repairs_legacy_system_heartbeat_payload(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            store.jobs_path.write_text(
                json.dumps(
                    [
                        {
                            "id": SYSTEM_HEARTBEAT_JOB_ID,
                            "name": "System heartbeat",
                            "prompt": SYSTEM_HEARTBEAT_DEFAULT_PROMPT,
                            "lane": "fast",
                            "schedule_kind": "every",
                            "run_at": "",
                            "interval_seconds": 900,
                            "enabled": True,
                            "system": False,
                            "source": "user",
                            "created_at": "2026-04-12T15:59:47Z",
                            "updated_at": "2026-04-12T15:59:47Z",
                            "last_run_at": "",
                            "next_run_at": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            job = store.ensure_system_heartbeat_job()
            persisted = json.loads(store.jobs_path.read_text(encoding="utf-8"))[0]

            self.assertTrue(job.system)
            self.assertEqual(job.source, "system")
            self.assertIsNotNone(parse_iso_datetime(job.next_run_at))
            self.assertTrue(persisted["system"])
            self.assertEqual(persisted["source"], "system")
            self.assertTrue(persisted["next_run_at"])
            self.assertNotEqual(persisted["updated_at"], "2026-04-12T15:59:47Z")

    def test_upsert_and_mark_run_preserve_system_identity(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))
            store.ensure_system_heartbeat_job()

            saved = store.upsert_job(
                {
                    "id": SYSTEM_HEARTBEAT_JOB_ID,
                    "name": "System heartbeat",
                    "prompt": "Updated heartbeat prompt",
                    "lane": "auto",
                    "schedule_kind": "at",
                    "run_at": "2026-05-01T10:00:00Z",
                    "interval_seconds": 300,
                    "enabled": False,
                }
            )
            marked = store.mark_job_run(
                SYSTEM_HEARTBEAT_JOB_ID,
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

            self.assertTrue(saved.system)
            self.assertEqual(saved.source, "system")
            self.assertEqual(saved.schedule_kind, "every")
            self.assertEqual(saved.run_at, "")
            self.assertFalse(saved.enabled)
            self.assertIsNotNone(marked)
            assert marked is not None
            self.assertTrue(marked.system)
            self.assertEqual(marked.source, "system")
            self.assertEqual(marked.next_run_at, "2026-01-01T00:05:00Z")

    def test_delete_system_heartbeat_is_blocked_by_fixed_id(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))
            store.ensure_system_heartbeat_job()

            with self.assertRaises(ValueError):
                store.delete_job(SYSTEM_HEARTBEAT_JOB_ID)

    def test_cron_job_computes_next_run_with_croniter(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))

            job = store.upsert_job(
                {
                    "name": "Hourly check",
                    "prompt": "Review Inbox.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "0 * * * *",
                    "enabled": True,
                }
            )
            run_time = datetime(2026, 1, 1, 10, 15, tzinfo=timezone.utc)
            marked = store.mark_job_run(job.id, run_time)
            expected_next_run = to_iso_z(croniter("0 * * * *", run_time.astimezone()).get_next(datetime))

            self.assertEqual(job.schedule_kind, "cron")
            self.assertEqual(job.cron_expression, "0 * * * *")
            self.assertTrue(job.next_run_at)
            self.assertIsNotNone(marked)
            assert marked is not None
            self.assertEqual(marked.next_run_at, expected_next_run)

    def test_cron_job_rejects_non_linux_field_counts(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))

            with self.assertRaisesRegex(ValueError, "5-field cron expression"):
                store.upsert_job(
                    {
                        "name": "Seconds cron",
                        "prompt": "Run too frequently.",
                        "lane": "fast",
                        "schedule_kind": "cron",
                        "cron_expression": "*/10 * * * * *",
                        "enabled": True,
                    }
                )

    def test_load_jobs_disables_invalid_persisted_cron(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            store.jobs_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "bad-cron",
                            "name": "Bad cron",
                            "prompt": "Run on a broken schedule.",
                            "lane": "fast",
                            "schedule_kind": "cron",
                            "cron_expression": "*/10 * * * * *",
                            "enabled": True,
                            "next_run_at": "2026-01-01T00:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            job = store.load_jobs()[0]
            marked = store.mark_job_run("bad-cron", datetime(2026, 1, 1, tzinfo=timezone.utc))

            self.assertFalse(job.enabled)
            self.assertEqual(job.next_run_at, "")
            self.assertIsNotNone(marked)
            assert marked is not None
            self.assertFalse(marked.enabled)
            self.assertEqual(marked.next_run_at, "")

    def test_load_jobs_only_fixed_id_is_system_job(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            store.jobs_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "user-job",
                            "name": "User job",
                            "prompt": "Send a reminder.",
                            "lane": "fast",
                            "schedule_kind": "every",
                            "interval_seconds": 300,
                            "enabled": True,
                            "system": True,
                            "source": "system",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            job = store.load_jobs()[0]

            self.assertFalse(job.system)
            self.assertEqual(job.source, "user")
            self.assertTrue(store.delete_job(job.id))

    def test_load_jobs_repairs_missing_next_run_for_enabled_recurring_jobs(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            store.jobs_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "legacy-every",
                            "name": "Legacy interval",
                            "prompt": "Send a reminder.",
                            "lane": "fast",
                            "schedule_kind": "every",
                            "interval_seconds": 300,
                            "enabled": True,
                            "last_run_at": "2026-01-01T00:00:00Z",
                            "next_run_at": "",
                        },
                        {
                            "id": "legacy-cron",
                            "name": "Legacy cron",
                            "prompt": "Send a cron reminder.",
                            "lane": "fast",
                            "schedule_kind": "cron",
                            "cron_expression": "0 * * * *",
                            "enabled": True,
                            "last_run_at": "2026-01-01T00:15:00Z",
                            "next_run_at": "",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            jobs = {job.id: job for job in store.load_jobs()}

            self.assertEqual(jobs["legacy-every"].next_run_at, "2026-01-01T00:05:00Z")
            self.assertTrue(jobs["legacy-cron"].next_run_at)
            self.assertIsNotNone(parse_iso_datetime(jobs["legacy-cron"].next_run_at))


class DummyOrchestrator:
    def __init__(
        self,
        memory: SafeMemory | None = None,
        runtime_manager: object | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self.memory = memory
        self.runtime_manager = runtime_manager

    async def process_message(self, **kwargs):
        self.calls.append(kwargs)
        return "heartbeat-result"


class FakeRuntimeResult:
    text = "Hola. Recuerda ponerte con el trabajo que tienes que enviar."


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_messages(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRuntimeResult()


class RuntimeSchedulerHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_heartbeat_injects_active_checklist(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))
            store.ensure_system_heartbeat_job()
            orchestrator = DummyOrchestrator()
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)
            scheduler._load_heartbeat_text = lambda: "- Review Inbox"

            result = await scheduler.run_job_now(SYSTEM_HEARTBEAT_JOB_ID)

            self.assertTrue(result["ok"])
            self.assertEqual(result["response"], "heartbeat-result")
            self.assertEqual(len(orchestrator.calls), 1)
            call = orchestrator.calls[0]
            self.assertEqual(call["user_id"], f"cron:{SYSTEM_HEARTBEAT_JOB_ID}")
            self.assertEqual(call["source"], "heartbeat")
            self.assertEqual(call["title"], "Workspace heartbeat")
            self.assertFalse(call["store_memory"])
            self.assertIn("Active checklist:\n- Review Inbox", call["user_message"])
            updated = store.load_jobs()[0]
            self.assertTrue(updated.last_run_at)
            self.assertTrue(updated.system)
            self.assertEqual(updated.source, "system")

    async def test_system_heartbeat_skips_empty_checklist(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))
            store.ensure_system_heartbeat_job()
            orchestrator = DummyOrchestrator()
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)
            scheduler._load_heartbeat_text = lambda: ""

            result = await scheduler.run_job_now(SYSTEM_HEARTBEAT_JOB_ID)

            self.assertTrue(result["ok"])
            self.assertEqual(result["response"], "HEARTBEAT_SKIP")
            self.assertEqual(result["delivery"], {"kind": "none"})
            self.assertTrue(result["next_run_at"])
            self.assertEqual(orchestrator.calls, [])
            updated = store.load_jobs()[0]
            self.assertTrue(updated.last_run_at)

    async def test_custom_heartbeat_delivers_response_to_desktop_chat(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting and remind me to work.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            orchestrator = DummyOrchestrator(memory=memory)
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            result = await scheduler.run_job_now(job.id)

            self.assertTrue(result["ok"])
            delivery = result["delivery"]
            self.assertEqual(delivery["kind"], "desktop_chat")
            self.assertEqual(delivery["user_id"], "desktop-user")
            self.assertEqual(delivery["conversation_title"], "Heartbeat: Work reminder")
            conversation_id = delivery["conversation_id"]
            messages = memory.get_conversation_messages(conversation_id)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["role"], "assistant")
            self.assertEqual(messages[0]["content"], "heartbeat-result")
            updated = next(item for item in store.load_jobs() if item.id == job.id)
            self.assertEqual(updated.delivery_conversation_id, conversation_id)
            self.assertTrue(updated.last_run_at)
            memory.close()

    async def test_job_run_reports_persistence_error(self) -> None:
        with temp_runtime_dir() as tmp:
            store = make_store(Path(tmp))
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )
            orchestrator = DummyOrchestrator()
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            def fail_mark_job_run(*args, **kwargs):
                raise RuntimeError("disk unavailable")

            store.mark_job_run = fail_mark_job_run  # type: ignore[method-assign]

            with self.assertLogs("azul_backend.azul_brain.runtime.scheduler", level="ERROR"):
                result = await scheduler.run_job_now(job.id)

            self.assertFalse(result["ok"])
            self.assertEqual(result["response"], "heartbeat-result")
            self.assertIn("Persistence error: disk unavailable", result["error"])
            self.assertEqual(
                result["delivery"],
                {"kind": "none", "error": "delivery skipped after persistence failure"},
            )

    async def test_custom_heartbeat_generation_is_isolated_from_desktop_chat_history(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting and remind me to work.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            active_conversation_id = memory.create_conversation(
                "desktop-user",
                "Main conversation",
            )
            memory.set_active_conversation("desktop-user", active_conversation_id)
            orchestrator = DummyOrchestrator(memory=memory)
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            result = await scheduler.run_job_now(job.id)

            self.assertTrue(result["ok"])
            self.assertEqual(len(orchestrator.calls), 1)
            call = orchestrator.calls[0]
            self.assertEqual(call["user_id"], f"cron:{job.id}")
            self.assertEqual(call["source"], "cron")
            self.assertIn("Write the exact desktop chat message", call["user_message"])
            self.assertIn("Scheduled task:\nSend me a greeting", call["user_message"])
            self.assertFalse(call["store_memory"])
            self.assertEqual(result["delivery"]["conversation_id"], active_conversation_id)
            memory.close()

    async def test_delivery_error_does_not_fail_job_run(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )

            class FailingMemory:
                def get_active_conversation_id(self, user_id):
                    raise RuntimeError("sqlite locked")

            orchestrator = DummyOrchestrator(memory=FailingMemory())
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            with self.assertLogs("azul_backend.azul_brain.runtime.scheduler", level="ERROR"):
                result = await scheduler.run_job_now(job.id)

            self.assertTrue(result["ok"])
            self.assertEqual(result["delivery"], {"kind": "none", "error": "sqlite locked"})
            updated = next(item for item in store.load_jobs() if item.id == job.id)
            self.assertTrue(updated.last_run_at)

    async def test_message_persistence_failure_reports_delivery_error(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )

            class WriteFailingMemory:
                def get_active_conversation_id(self, user_id):
                    return ""

                def get_conversation_title(self, conversation_id):
                    return ""

                def conversation_exists(self, conversation_id):
                    return True

                def get_or_create_named_conversation(self, user_id, title):
                    return "conv-1", title

                def add_message(self, user_id, role, content, conversation_id=None):
                    return False

            orchestrator = DummyOrchestrator(memory=WriteFailingMemory())
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            result = await scheduler.run_job_now(job.id)

            self.assertTrue(result["ok"])
            self.assertEqual(result["delivery"], {"kind": "none", "error": "message persistence failed"})
            updated = next(item for item in store.load_jobs() if item.id == job.id)
            self.assertTrue(updated.last_run_at)

    async def test_custom_heartbeat_uses_no_tool_runtime_when_available(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting and remind me to work.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            runtime_manager = FakeRuntimeManager()
            orchestrator = DummyOrchestrator(
                memory=memory,
                runtime_manager=runtime_manager,
            )
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            result = await scheduler.run_job_now(job.id)

            self.assertTrue(result["ok"])
            self.assertEqual(orchestrator.calls, [])
            self.assertEqual(len(runtime_manager.calls), 1)
            call = runtime_manager.calls[0]
            self.assertFalse(call["tools_enabled"])
            self.assertIn("proactive desktop chat message", call["instructions"])
            message_text = "".join(
                getattr(content, "text", "")
                for content in call["messages"][0].contents
            )
            self.assertIn("Scheduled task:\nSend me a greeting", message_text)
            conversation_id = result["delivery"]["conversation_id"]
            messages = memory.get_conversation_messages(conversation_id)
            self.assertEqual(
                messages[0]["content"],
                "Hola. Recuerda ponerte con el trabajo que tienes que enviar.",
            )
            memory.close()

    async def test_custom_heartbeat_prefers_active_desktop_conversation(self) -> None:
        with temp_runtime_dir() as tmp:
            root = Path(tmp)
            store = make_store(root)
            job = store.upsert_job(
                {
                    "name": "Work reminder",
                    "prompt": "Send me a greeting and remind me to work.",
                    "lane": "fast",
                    "schedule_kind": "cron",
                    "cron_expression": "* * * * *",
                    "enabled": True,
                }
            )
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            active_conversation_id = memory.create_conversation(
                "desktop-user",
                "Main conversation",
            )
            memory.set_active_conversation("desktop-user", active_conversation_id)
            orchestrator = DummyOrchestrator(memory=memory)
            scheduler = RuntimeScheduler(store=store, orchestrator=orchestrator)

            result = await scheduler.run_job_now(job.id)

            self.assertTrue(result["ok"])
            delivery = result["delivery"]
            self.assertEqual(delivery["kind"], "desktop_chat")
            self.assertEqual(delivery["conversation_id"], active_conversation_id)
            self.assertEqual(delivery["conversation_title"], "Main conversation")
            messages = memory.get_conversation_messages(active_conversation_id)
            self.assertEqual(len(messages), 1)
            self.assertIn("heartbeat-result", messages[0]["content"])
            memory.close()


if __name__ == "__main__":
    unittest.main()
