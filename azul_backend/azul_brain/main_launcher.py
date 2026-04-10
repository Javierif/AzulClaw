"""Punto de entrada HTTP de AzulClaw y ciclo de vida del proceso principal."""

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
else:
    from .api.routes import register_desktop_routes
    from .bootstrap import build_adapter, build_mcp_client
    from .bot.azul_bot import AzulBot
    from .config import HOST, load_runtime_config
    from .conversation import ConversationOrchestrator

LOGGER = logging.getLogger(__name__)


@web.middleware
async def cors_middleware(req: web.Request, handler):
    """Aplica cabeceras CORS simples para la desktop app en desarrollo."""
    if req.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(req)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    return response


async def messages_handler(req: web.Request) -> web.Response:
    """Endpoint de mensajes de Azure Bot Service."""
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
    """Inicializa app HTTP, cliente MCP, bot y API desktop."""
    LOGGER.info("Despertando el Cerebro de AzulClaw...")

    base_path = Path(__file__).resolve().parent
    runtime_config = load_runtime_config(base_path)

    adapter = build_adapter(runtime_config.app_id, runtime_config.app_password)
    mcp_client = build_mcp_client(base_path)

    try:
        await mcp_client.connect()
    except Exception as error:
        LOGGER.error(
            "Fallo critico: No se pudo conectar las Manos (MCP Server). "
            "El Bot sera de solo lectura. %s",
            error,
        )

    orchestrator = ConversationOrchestrator(mcp_client)

    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = AzulBot(orchestrator)
    app["adapter"] = adapter
    app["mcp_client"] = mcp_client
    app["orchestrator"] = orchestrator
    app.router.add_post("/api/messages", messages_handler)
    register_desktop_routes(app)
    return app


async def main() -> None:
    """Arranca el servidor HTTP y mantiene el proceso en ejecucion."""
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
            "Servidor local HTTP escuchando en http://%s:%s",
            HOST,
            port,
        )
        await asyncio.Event().wait()
    except OSError as error:
        if getattr(error, "winerror", None) == 10048:
            LOGGER.error(
                "El puerto %s ya esta en uso. Cierra la otra instancia de AzulClaw o "
                "cambia la variable PORT.",
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
                LOGGER.warning("Error al cerrar MCP Client: %s", cleanup_error)
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("AzulClaw detenido por usuario.")
    except Exception as error:
        LOGGER.error("Error mortal: %s", error)
