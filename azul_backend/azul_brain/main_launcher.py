"""AzulClaw HTTP entry point and main process lifecycle."""

import asyncio
import logging
import sys
from pathlib import Path

from aiohttp import web
from botbuilder.schema import Activity

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from azul_backend.azul_brain.api.routes import register_desktop_routes
    from azul_backend.azul_brain.bootstrap import build_adapter, build_mcp_client
    from azul_backend.azul_brain.bot.azul_bot import AzulBot
    from azul_backend.azul_brain.config import HOST, load_runtime_config
    from azul_backend.azul_brain.conversation import ConversationOrchestrator
    from azul_backend.azul_brain.runtime.agent_runtime import AgentRuntimeManager
    from azul_backend.azul_brain.runtime.process_registry import ProcessRegistry
    from azul_backend.azul_brain.runtime.scheduler import RuntimeScheduler
    from azul_backend.azul_brain.runtime.store import RuntimeStore
else:
    from .api.routes import register_desktop_routes
    from .bootstrap import build_adapter, build_mcp_client
    from .bot.azul_bot import AzulBot
    from .config import HOST, load_runtime_config
    from .conversation import ConversationOrchestrator
    from .runtime.agent_runtime import AgentRuntimeManager
    from .runtime.process_registry import ProcessRegistry
    from .runtime.scheduler import RuntimeScheduler
    from .runtime.store import RuntimeStore

LOGGER = logging.getLogger(__name__)
CORS_ALLOWED_METHODS = "GET,POST,PUT,DELETE,OPTIONS"
CORS_ALLOWED_HEADERS = "Content-Type,Authorization"


def apply_cors_headers(req: web.Request, response: web.StreamResponse) -> None:
    """Applies CORS headers also to streaming responses and framework errors."""
    origin = req.headers.get("Origin", "").strip()
    requested_headers = req.headers.get("Access-Control-Request-Headers", "").strip()

    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOWED_METHODS
    response.headers["Access-Control-Allow-Headers"] = requested_headers or CORS_ALLOWED_HEADERS
    response.headers["Access-Control-Max-Age"] = "600"
    if origin:
        response.headers["Vary"] = "Origin"


async def cors_on_prepare(req: web.Request, response: web.StreamResponse) -> None:
    """Injects CORS headers just before sending, including on StreamResponse."""
    apply_cors_headers(req, response)


@web.middleware
async def cors_middleware(req: web.Request, handler):
    """Applies simple CORS headers for the desktop app in development."""
    if req.method == "OPTIONS":
        response = web.Response(status=204)
        apply_cors_headers(req, response)
        return response
    return await handler(req)


async def messages_handler(req: web.Request) -> web.Response:
    """Azure Bot Service messages endpoint."""
    bot = req.app["bot"]
    adapter = req.app["adapter"]

    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415)

    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")
    response = await adapter.process_activity(activity, auth_header, bot.on_turn)

    if response:
        return web.json_response(data=response.body, status=response.status)
    return web.Response(status=201)


async def create_app() -> web.Application:
    """Initialises the HTTP app, MCP client, bot, and desktop API."""
    LOGGER.info("Waking up AzulClaw's brain...")

    base_path = Path(__file__).resolve().parent
    runtime_config = load_runtime_config(base_path)

    adapter = build_adapter(runtime_config.app_id, runtime_config.app_password)
    mcp_client = build_mcp_client(base_path)

    try:
        await mcp_client.connect()
    except Exception as error:
        LOGGER.error(
            "Critical failure: could not connect to the Hands (MCP Server). "
            "Bot will be read-only. %s",
            error,
        )

    runtime_store = RuntimeStore()
    process_registry = ProcessRegistry(runtime_store)
    runtime_manager = AgentRuntimeManager(
        mcp_client=mcp_client,
        store=runtime_store,
        process_registry=process_registry,
    )
    orchestrator = ConversationOrchestrator(mcp_client, runtime_manager)
    scheduler = RuntimeScheduler(store=runtime_store, orchestrator=orchestrator)
    await scheduler.start()

    app = web.Application(middlewares=[cors_middleware])
    app.on_response_prepare.append(cors_on_prepare)
    app["bot"] = AzulBot(orchestrator)
    app["adapter"] = adapter
    app["mcp_client"] = mcp_client
    app["orchestrator"] = orchestrator
    app["runtime_store"] = runtime_store
    app["process_registry"] = process_registry
    app["runtime_manager"] = runtime_manager
    app["scheduler"] = scheduler
    app.router.add_post("/api/messages", messages_handler)
    register_desktop_routes(app)
    return app


async def main() -> None:
    """Starts the HTTP server and keeps the process running."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    base_path = Path(__file__).resolve().parent
    runtime_config = load_runtime_config(base_path)
    port = runtime_config.port

    app = await create_app()
    runner = web.AppRunner(app)

    try:
        await runner.setup()
        site = web.TCPSite(runner, host=HOST, port=port)
        await site.start()
        LOGGER.info(
            "Local HTTP server listening on http://%s:%s",
            HOST,
            port,
        )
        await asyncio.Event().wait()
    except OSError as error:
        if getattr(error, "winerror", None) == 10048:
            LOGGER.error(
                "Port %s is already in use. Close the other AzulClaw instance or "
                "change the PORT variable.",
                port,
            )
            return
        raise
    finally:
        mcp_client = app.get("mcp_client")
        if mcp_client is not None:
            try:
                await mcp_client.cleanup()
            except Exception as cleanup_error:
                LOGGER.warning("Error closing MCP Client: %s", cleanup_error)
        scheduler = app.get("scheduler")
        if scheduler is not None:
            try:
                await scheduler.stop()
            except Exception as scheduler_error:
                LOGGER.warning("Error stopping scheduler: %s", scheduler_error)
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("AzulClaw stopped by user.")
    except Exception as error:
        LOGGER.error("Fatal error: %s", error)
