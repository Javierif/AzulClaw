import asyncio
import os
import sys
import traceback

ENV_LOCAL_FILENAME = ".env.local"


def _load_env_file(filename: str = ENV_LOCAL_FILENAME) -> None:
    base_path = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_path, filename)
    if not os.path.exists(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            stripped_line = raw_line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                continue
            if "=" not in stripped_line:
                continue
            key, value = stripped_line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value


_load_env_file()

from aiohttp import web
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity

from .bot.azul_bot import AzulBot
from .mcp_client import AzulHandsClient

APP_ID = os.environ.get("MicrosoftAppId", "")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
PORT = int(os.environ.get("PORT", "3978"))

SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
ADAPTER = BotFrameworkAdapter(SETTINGS)


async def on_error(context: TurnContext, error: Exception):
    print(f"\n [Error Capturado del Sistema Cognitivo]: {error}", file=sys.stderr)
    traceback.print_exc()
    await context.send_activity(
        "El cerebro de AzulClaw ha encontrado un error. Reiniciando subsistemas."
    )
    await context.send_activity(f"Exception: {error}")


ADAPTER.on_turn_error = on_error


async def init_azulclaw() -> web.Application:
    print("[INIT] Despertando el Cerebro de AzulClaw...")

    base_path = os.path.dirname(os.path.abspath(__file__))
    mcp_script_path = os.path.join(base_path, "..", "azul_hands_mcp", "mcp_server.py")
    mcp_client = AzulHandsClient(mcp_script_path)

    try:
        await mcp_client.connect()
    except Exception as error:
        print(
            "[ERROR] Fallo Critico: No se pudo conectar las Manos (MCP Server). "
            f"El Bot sera de solo lectura. {error}"
        )

    app = web.Application()
    app["bot"] = AzulBot(mcp_client)
    app["adapter"] = ADAPTER
    app["mcp_client"] = mcp_client
    app.router.add_post("/api/messages", messages_handler)
    return app


async def _handle_activity(adapter, activity, auth_header, bot_turn):
    return await adapter.process_activity(activity, auth_header, bot_turn)


async def messages_handler(req: web.Request) -> web.Response:
    bot = req.app["bot"]
    adapter = req.app["adapter"]

    if "application/json" in req.headers.get("Content-Type", ""):
        body = await req.json()
    else:
        return web.Response(status=415)

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")
    response = await _handle_activity(adapter, activity, auth_header, bot.on_turn)

    if response:
        return web.json_response(data=response.body, status=response.status)
    return web.Response(status=201)


async def main():
    app = await init_azulclaw()
    runner = web.AppRunner(app)

    try:
        await runner.setup()
        site = web.TCPSite(runner, host="localhost", port=PORT)
        await site.start()
        print(
            "[INFO] Servidor local HTTP escuchando a Azure Bot en "
            f"http://localhost:{PORT}/api/messages"
        )

        await asyncio.Event().wait()
    except OSError as error:
        if getattr(error, "winerror", None) == 10048:
            print(
                f"[ERROR] El puerto {PORT} ya esta en uso. "
                "Cierra la otra instancia de AzulClaw o cambia la variable PORT."
            )
            return
        raise
    finally:
        mcp_client = app.get("mcp_client")
        if mcp_client is not None:
            try:
                await mcp_client.cleanup()
            except Exception as cleanup_error:
                print(f"[WARN] Error al cerrar MCP Client: {cleanup_error}")
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[INFO] AzulClaw detenido por usuario.")
    except Exception as error:
        print(f"Error mortal: {error}")
