from __future__ import annotations

import asyncio
import shutil
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from azul_backend.azul_brain import mcp_client as mcp_client_module
except ModuleNotFoundError:
    mcp_client_module = None


class _TaskBoundAsyncContext:
    def __init__(self, value):
        self.value = value
        self.enter_task = None
        self.exit_task = None

    async def __aenter__(self):
        self.enter_task = asyncio.current_task()
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_task = asyncio.current_task()
        if self.exit_task is not self.enter_task:
            raise RuntimeError("task mismatch during async context cleanup")
        return False


class _FakeClientSession(_TaskBoundAsyncContext):
    def __init__(self, read_stream, write_stream):
        self.read_stream = read_stream
        self.write_stream = write_stream
        super().__init__(self)

    async def initialize(self):
        return None


class _DummyPrimaryClient:
    def __init__(self):
        self.cleaned = False

    async def cleanup(self) -> None:
        self.cleaned = True

    async def list_available_tools(self):
        return []

    async def call_tool(self, tool_name: str, arguments: dict):
        return {"tool_name": tool_name, "arguments": arguments}


class _PassiveCleanupClient:
    def __init__(self):
        self.cleaned = False

    async def cleanup(self) -> None:
        self.cleaned = True


class _MutatingCleanupClient:
    def __init__(self, mux):
        self.mux = mux
        self.cleaned = False

    async def cleanup(self) -> None:
        self.cleaned = True
        self.mux.skill_clients["late-added"] = _PassiveCleanupClient()


@unittest.skipIf(mcp_client_module is None, "mcp package is not installed in this test environment.")
class MCPClientLifecycleTests(unittest.TestCase):
    def test_default_backend_log_dir_stays_inside_repo_memory(self) -> None:
        log_dir = mcp_client_module._default_backend_log_dir()

        self.assertEqual(log_dir.name, "runtime-logs")
        self.assertEqual(log_dir.parent.name, "memory")
        self.assertEqual(log_dir.parent.parent.name, "AzulClaw")

    def test_azul_hands_client_cleanup_runs_on_owner_task(self) -> None:
        async def _exercise() -> tuple[_TaskBoundAsyncContext, _FakeClientSession]:
            transport_contexts: list[_TaskBoundAsyncContext] = []
            session_contexts: list[_FakeClientSession] = []

            def _fake_stdio_client(*args, **kwargs):
                context = _TaskBoundAsyncContext((object(), object()))
                transport_contexts.append(context)
                return context

            def _fake_client_session(read_stream, write_stream):
                session = _FakeClientSession(read_stream, write_stream)
                session_contexts.append(session)
                return session

            temp_dir = Path("memory") / "test-mcp-client" / "task-affinity"
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                with patch.object(mcp_client_module, "stdio_client", _fake_stdio_client), patch.object(
                    mcp_client_module,
                    "ClientSession",
                    _fake_client_session,
                ), patch.object(mcp_client_module, "_default_backend_log_dir", return_value=temp_dir):
                    client = mcp_client_module.AzulHandsClient("ignored.py", label="task-affinity")
                    await asyncio.create_task(client.connect())
                    await asyncio.create_task(client.cleanup())
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

            return transport_contexts[0], session_contexts[0]

        transport_context, session_context = asyncio.run(_exercise())

        self.assertIsNotNone(transport_context.enter_task)
        self.assertIs(transport_context.enter_task, transport_context.exit_task)
        self.assertIsNotNone(session_context.enter_task)
        self.assertIs(session_context.enter_task, session_context.exit_task)

    def test_multiplexer_cleanup_uses_client_snapshot(self) -> None:
        async def _exercise() -> tuple[bool, bool, bool]:
            primary_client = _DummyPrimaryClient()
            mux = mcp_client_module.AzulMCPMultiplexer(primary_client, skill_specs_provider=lambda: [])
            mutating_client = _MutatingCleanupClient(mux)
            mux.skill_clients = {"skill-one": mutating_client}
            mux.skill_tool_catalog = {"skill-one": []}
            mux.skill_runtime_status = {"skill-one": {"status": "connected"}}

            await mux.cleanup()
            return mutating_client.cleaned, primary_client.cleaned, not mux.skill_clients

        mutating_cleaned, primary_cleaned, cleared = asyncio.run(_exercise())

        self.assertTrue(mutating_cleaned)
        self.assertTrue(primary_cleaned)
        self.assertTrue(cleared)
