"""Rutas HTTP para la desktop app."""

from __future__ import annotations

from aiohttp import web

from .services import (
    list_workspace_entries,
    summarize_memory,
    summarize_processes,
)


async def health_handler(_: web.Request) -> web.Response:
    """Devuelve estado basico del backend local."""
    return web.json_response({"status": "ok"})


async def desktop_chat_handler(req: web.Request) -> web.Response:
    """Procesa un mensaje desde la app desktop."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = str(payload.get("user_id", "desktop-user")).strip() or "desktop-user"
    message = str(payload.get("message", "")).strip()

    if not message:
        return web.json_response({"error": "message is required"}, status=400)

    reply = await orchestrator.process_user_message(user_id, message)
    history = orchestrator.memory.get_history(user_id, limit=12)
    return web.json_response(
        {
            "user_id": user_id,
            "reply": reply,
            "history": history,
        }
    )


async def desktop_processes_handler(_: web.Request) -> web.Response:
    """Devuelve el resumen de procesos visibles para la desktop app."""
    return web.json_response({"items": summarize_processes()})


async def desktop_memory_handler(req: web.Request) -> web.Response:
    """Devuelve una vista resumida de memoria para la desktop app."""
    orchestrator = req.app["orchestrator"]
    user_id = req.query.get("user_id", "desktop-user")
    return web.json_response({"items": summarize_memory(orchestrator, user_id)})


async def desktop_workspace_handler(req: web.Request) -> web.Response:
    """Lista contenido del workspace sandbox."""
    relative_path = req.query.get("path", ".")
    try:
        listing = list_workspace_entries(relative_path)
    except Exception as error:
        return web.json_response({"error": str(error)}, status=400)

    return web.json_response(listing)


def register_desktop_routes(app: web.Application) -> None:
    """Registra endpoints consumidos por la app desktop."""
    app.router.add_get("/api/health", health_handler)
    app.router.add_post("/api/desktop/chat", desktop_chat_handler)
    app.router.add_get("/api/desktop/processes", desktop_processes_handler)
    app.router.add_get("/api/desktop/memory", desktop_memory_handler)
    app.router.add_get("/api/desktop/workspace", desktop_workspace_handler)
