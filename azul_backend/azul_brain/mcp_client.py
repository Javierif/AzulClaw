"""MCP clients for connecting AzulBrain to built-in and skill MCP servers."""

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .runtime.pending_action_intent import (
    FOLDER_ORGANIZER_SKILL_ID,
    maybe_record_folder_organizer_preview,
)

LOGGER = logging.getLogger(__name__)


def _force_windows_popen_stdio() -> None:
    """Avoids asyncio pipe creation failures seen on Windows/Python 3.13."""
    if sys.platform != "win32":
        return

    try:
        import mcp.client.stdio as stdio_module
        from mcp.os.win32.utilities import FallbackProcess
    except Exception:
        return

    async def create_windows_process(command, args, env=None, errlog=sys.stderr, cwd=None):
        popen_obj = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errlog,
            env=env,
            cwd=cwd,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return FallbackProcess(popen_obj)

    stdio_module.create_windows_process = create_windows_process


def _format_tool_names(tools: list[Any]) -> str:
    """Returns a readable list of published MCP tool names."""
    names = [getattr(tool, "name", str(tool)) for tool in tools]
    return ", ".join(names) if names else "no tools"


def _log_filename(label: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in label).strip("-")
    return f"{normalized or 'mcp'}.err.log"


def _default_backend_log_dir() -> Path:
    override = os.environ.get("AZUL_BACKEND_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    runtime_override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if runtime_override:
        return Path(runtime_override).expanduser().parent / "logs"
    return Path(__file__).resolve().parents[2] / "memory" / "runtime-logs"


class AzulHandsClient:
    """Generic stdio MCP client used by the built-in MCP and skill MCP runtimes."""

    def __init__(
        self,
        server_script_path: str,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | Path | None = None,
        *,
        env: dict[str, str] | None = None,
        label: str = "AzulHands",
    ):
        self.label = label
        merged_env = os.environ.copy()
        if env:
            merged_env.update({key: str(value) for key, value in env.items() if str(value).strip()})
        self.server_parameters = StdioServerParameters(
            command=command or sys.executable,
            args=args if args is not None else [server_script_path],
            env=merged_env,
            cwd=cwd,
        )
        self.session: ClientSession | None = None
        self.read_stream = None
        self.write_stream = None
        self._owner_task: asyncio.Task[None] | None = None
        self._ready_event: asyncio.Event | None = None
        self._close_event: asyncio.Event | None = None
        self._connect_error: Exception | None = None

    async def _run_connection(self) -> None:
        exit_stack = contextlib.AsyncExitStack()
        try:
            _force_windows_popen_stdio()
            log_path = _default_backend_log_dir() / _log_filename(self.label)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            errlog = exit_stack.enter_context(log_path.open("a", encoding="utf-8"))
            stdio_transport = await exit_stack.enter_async_context(
                stdio_client(self.server_parameters, errlog=errlog)
            )
            self.read_stream, self.write_stream = stdio_transport
            self.session = await exit_stack.enter_async_context(
                ClientSession(self.read_stream, self.write_stream)
            )
            await self.session.initialize()
            self._connect_error = None
            LOGGER.info("MCP connection established for %s.", self.label)
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()
            if self._close_event is not None:
                await self._close_event.wait()
        except Exception as error:
            self._connect_error = error
            LOGGER.exception("Error connecting MCP client %s: %s", self.label, error)
            if self._ready_event is not None and not self._ready_event.is_set():
                self._ready_event.set()
        finally:
            self.session = None
            self.read_stream = None
            self.write_stream = None
            try:
                await exit_stack.aclose()
            except Exception as error:
                LOGGER.warning("Error closing MCP connection for %s: %s", self.label, error)
            finally:
                if self._ready_event is not None and not self._ready_event.is_set():
                    self._ready_event.set()

    async def connect(self) -> None:
        """Starts the STDIO transport, creates an MCP session, and runs initialize()."""
        if self._owner_task is not None and not self._owner_task.done():
            if self._ready_event is not None:
                await self._ready_event.wait()
            if self._connect_error is not None:
                raise self._connect_error
            if self.session is None:
                raise RuntimeError(f"MCP client {self.label} did not finish connecting.")
            return

        LOGGER.info("Connecting MCP client: %s", self.label)
        self._connect_error = None
        self._ready_event = asyncio.Event()
        self._close_event = asyncio.Event()
        self._owner_task = asyncio.create_task(
            self._run_connection(),
            name=f"mcp-client-{self.label}",
        )
        await self._ready_event.wait()
        if self._connect_error is not None:
            error = self._connect_error
            await self.cleanup()
            raise error
        if self.session is None:
            await self.cleanup()
            raise RuntimeError(f"MCP client {self.label} did not finish connecting.")

    async def list_available_tools(self) -> list[Any]:
        """Retrieves the tool catalogue published by the MCP server."""
        if not self.session:
            raise RuntimeError(f"No active MCP session for {self.label}.")
        response = await self.session.list_tools()
        return response.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Invokes a remote MCP tool with serialisable arguments."""
        if not self.session:
            raise RuntimeError(f"No active MCP session for {self.label}.")
        LOGGER.info("Executing MCP tool '%s' on %s with arguments %s", tool_name, self.label, arguments)
        return await self.session.call_tool(tool_name, arguments)

    async def cleanup(self) -> None:
        """Closes the session and transport resources of the MCP child process."""
        LOGGER.info("Closing MCP connection for %s...", self.label)
        owner_task = self._owner_task
        close_event = self._close_event
        if close_event is not None:
            close_event.set()
        if owner_task is None:
            return
        try:
            await owner_task
        finally:
            if self._owner_task is owner_task:
                self._owner_task = None
                self._ready_event = None
                self._close_event = None
                self._connect_error = None


class AzulMCPMultiplexer:
    """Routes MCP calls to the built-in AzulHands server and enabled skill runtimes."""

    def __init__(
        self,
        primary_client: AzulHandsClient,
        skill_specs_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ):
        self.primary_client = primary_client
        self.skill_specs_provider = skill_specs_provider or (lambda: [])
        self.skill_clients: dict[str, AzulHandsClient] = {}
        self.skill_tool_catalog: dict[str, list[dict[str, Any]]] = {}
        self.skill_runtime_status: dict[str, dict[str, Any]] = {}
        self._lifecycle_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connects the built-in MCP and then any enabled skill MCP runtimes."""
        primary_error: Exception | None = None
        try:
            await self.primary_client.connect()
        except Exception as error:
            primary_error = error
        await self.reload_skill_clients()
        if primary_error is not None:
            raise primary_error

    async def reload_skill_clients(self) -> None:
        """Reconnects skill MCP runtimes from the current installed skill state."""
        async with self._lifecycle_lock:
            previous_clients = list(self.skill_clients.items())
            next_clients: dict[str, AzulHandsClient] = {}
            next_tool_catalog: dict[str, list[dict[str, Any]]] = {}
            next_runtime_status: dict[str, dict[str, Any]] = {}

            for skill_id, client in previous_clients:
                try:
                    await client.cleanup()
                except Exception as error:
                    LOGGER.warning("Error closing skill MCP runtime %s: %s", skill_id, error)

            for spec in self.skill_specs_provider():
                skill_id = str(spec.get("skill_id", "")).strip()
                if not skill_id:
                    continue
                label = f"skill-{skill_id}"
                client = AzulHandsClient(
                    str(spec.get("command", "")),
                    command=str(spec.get("command", "")).strip() or None,
                    args=[str(item) for item in spec.get("args", []) if str(item).strip()],
                    cwd=spec.get("cwd"),
                    env=spec.get("env", {}) if isinstance(spec.get("env", {}), dict) else {},
                    label=label,
                )
                try:
                    await client.connect()
                    tools = await client.list_available_tools()
                    next_clients[skill_id] = client
                    next_tool_catalog[skill_id] = [
                        {
                            "skill_id": skill_id,
                            "skill_name": str(spec.get("skill_name", skill_id)),
                            "tool_name": str(getattr(tool, "name", "")),
                            "description": str(getattr(tool, "description", "")),
                            "input_schema": getattr(tool, "inputSchema", None),
                        }
                        for tool in tools
                        if str(getattr(tool, "name", "")).strip()
                    ]
                    next_runtime_status[skill_id] = {
                        "skill_id": skill_id,
                        "skill_name": str(spec.get("skill_name", skill_id)),
                        "status": "connected",
                        "tool_count": len(next_tool_catalog[skill_id]),
                        "message": f"Connected with {_format_tool_names(tools)}.",
                    }
                except Exception as error:
                    try:
                        await client.cleanup()
                    except Exception:
                        pass
                    next_runtime_status[skill_id] = {
                        "skill_id": skill_id,
                        "skill_name": str(spec.get("skill_name", skill_id)),
                        "status": "error",
                        "tool_count": 0,
                        "message": str(error),
                    }

            self.skill_clients = next_clients
            self.skill_tool_catalog = next_tool_catalog
            self.skill_runtime_status = next_runtime_status

    async def list_available_tools(self) -> list[Any]:
        """Returns the built-in MCP tool catalogue."""
        async with self._lifecycle_lock:
            return await self.primary_client.list_available_tools()

    async def list_tool_catalog(self, *, include_primary: bool = False) -> list[dict[str, Any]]:
        """Returns connected tool metadata for skill MCP runtimes."""
        async with self._lifecycle_lock:
            items: list[dict[str, Any]] = []
            if include_primary:
                try:
                    primary_tools = await self.primary_client.list_available_tools()
                    items.extend({
                        "skill_id": "core.azulclaw.hands",
                        "skill_name": "AzulHands",
                        "tool_name": str(getattr(tool, "name", "")),
                        "description": str(getattr(tool, "description", "")),
                        "input_schema": getattr(tool, "inputSchema", None),
                    } for tool in primary_tools if str(getattr(tool, "name", "")).strip())
                except Exception as error:
                    LOGGER.warning("Could not read primary MCP tool catalog: %s", error)
            for catalog in self.skill_tool_catalog.values():
                items.extend(catalog)
            return items

    def get_skill_runtime_status(self) -> list[dict[str, Any]]:
        """Returns per-skill MCP runtime status for enabled local skills."""
        return [self.skill_runtime_status[key] for key in sorted(self.skill_runtime_status)]

    async def call_tool(self, tool_name: str, arguments: dict, *, skill_id: str | None = None) -> Any:
        """Routes a tool call to the built-in MCP or a connected skill MCP runtime."""
        async with self._lifecycle_lock:
            if skill_id:
                client = self.skill_clients.get(skill_id)
                if client is None:
                    raise RuntimeError(f"Skill MCP runtime '{skill_id}' is not connected.")
                result = await client.call_tool(tool_name, arguments)
                if skill_id == FOLDER_ORGANIZER_SKILL_ID:
                    try:
                        maybe_record_folder_organizer_preview(
                            tool_name=tool_name,
                            arguments=arguments,
                            result=result,
                        )
                    except Exception as error:
                        LOGGER.debug("Could not capture Folder Organizer preview: %s", error)
                return result
            return await self.primary_client.call_tool(tool_name, arguments)

    async def cleanup(self) -> None:
        """Closes all MCP sessions."""
        async with self._lifecycle_lock:
            clients = list(self.skill_clients.items())
            self.skill_clients = {}
            self.skill_tool_catalog = {}
            self.skill_runtime_status = {}
            for skill_id, client in clients:
                try:
                    await client.cleanup()
                except Exception as error:
                    LOGGER.warning("Error closing skill MCP runtime %s: %s", skill_id, error)
            self.skill_clients = {}
            self.skill_tool_catalog = {}
            self.skill_runtime_status = {}
            await self.primary_client.cleanup()


async def _run_smoke_test() -> None:
    """Runs a minimal smoke test against the local MCP server."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    server_script_path = Path(__file__).resolve().parents[1] / "azul_hands_mcp" / "mcp_server.py"
    client = AzulHandsClient(str(server_script_path))

    try:
        await client.connect()
        tools = await client.list_available_tools()
        LOGGER.info("Available MCP tools: %s", _format_tool_names(tools))

        workspace_listing = await client.call_tool("list_workspace_files", {"path": "."})
        LOGGER.info("list_workspace_files response: %s", workspace_listing)

        denied_result = await client.call_tool(
            "read_safe_file",
            {"path": "../../../../../Windows/System32/drivers/etc/hosts"},
        )
        LOGGER.info("Path traversal block response: %s", denied_result)
    finally:
        await client.cleanup()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_run_smoke_test())
