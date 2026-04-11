"""Rutas HTTP para la desktop app."""

from __future__ import annotations

import json

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from .services import (
    list_workspace_entries,
    load_hatching_profile,
    save_hatching_profile,
    summarize_jobs,
    summarize_memory,
    summarize_processes,
    summarize_runtime,
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

    reply = await orchestrator.process_user_message(user_id, message, lane="auto")
    history = orchestrator.memory.get_history(user_id, limit=12)
    return web.json_response(
        {
            "user_id": user_id,
            "reply": reply.text,
            "history": history,
            "runtime": {
                "lane": reply.lane,
                "model_id": reply.model_id,
                "model_label": reply.model_label,
                "process_id": reply.process_id,
                "triage_reason": reply.triage_reason,
            },
        }
    )


async def desktop_chat_stream_handler(req: web.Request) -> web.StreamResponse:
    """Procesa un mensaje desde la app desktop y emite NDJSON incremental."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = str(payload.get("user_id", "desktop-user")).strip() or "desktop-user"
    message = str(payload.get("message", "")).strip()

    if not message:
        return web.json_response({"error": "message is required"}, status=400)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    response.enable_chunked_encoding()
    await response.prepare(req)
    stream_closed = False

    async def write_event(event: dict) -> bool:
        nonlocal stream_closed
        if stream_closed:
            return False
        chunk = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            await response.write(chunk)
            if hasattr(response, "drain"):
                await response.drain()
        except (ClientConnectionResetError, ConnectionResetError):
            stream_closed = True
            return False
        return True

    try:
        await write_event({"type": "start"})

        reply = await orchestrator.process_user_message_stream(
            user_id,
            message,
            lane="auto",
            on_delta=lambda text: write_event({"type": "delta", "text": text}),
            on_commentary=lambda text: write_event({"type": "commentary", "text": text}),
            on_progress=lambda progress: write_event({"type": "progress", "progress": progress}),
        )
        history = orchestrator.memory.get_history(user_id, limit=12)
        await write_event(
            {
                "type": "done",
                "reply": reply.text,
                "history": history,
                "runtime": {
                    "lane": reply.lane,
                    "model_id": reply.model_id,
                    "model_label": reply.model_label,
                    "process_id": reply.process_id,
                    "triage_reason": reply.triage_reason,
                },
            }
        )
    except Exception as error:
        if not stream_closed:
            await write_event({"type": "error", "message": str(error)})
    finally:
        if not stream_closed:
            try:
                await response.write_eof()
            except (ClientConnectionResetError, ConnectionResetError):
                pass

    return response


async def desktop_processes_handler(_: web.Request) -> web.Response:
    """Devuelve el resumen de procesos visibles para la desktop app."""
    return web.json_response({"items": summarize_processes(_.app["process_registry"])})


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


async def desktop_hatching_get_handler(_: web.Request) -> web.Response:
    """Devuelve el perfil actual de Hatching."""
    return web.json_response(load_hatching_profile())


async def desktop_hatching_put_handler(req: web.Request) -> web.Response:
    """Guarda el perfil de Hatching enviado por la desktop app."""
    payload = await req.json()
    return web.json_response(save_hatching_profile(payload))


async def desktop_runtime_get_handler(req: web.Request) -> web.Response:
    """Devuelve estado agregado del runtime local."""
    return web.json_response(
        summarize_runtime(
            req.app["runtime_manager"],
            req.app["scheduler"],
            req.app["process_registry"],
        )
    )


async def desktop_runtime_put_handler(req: web.Request) -> web.Response:
    """Persiste configuracion editable del runtime."""
    payload = await req.json()
    req.app["runtime_manager"].save_settings(payload)
    return web.json_response(
        summarize_runtime(
            req.app["runtime_manager"],
            req.app["scheduler"],
            req.app["process_registry"],
        )
    )


async def desktop_jobs_get_handler(req: web.Request) -> web.Response:
    """Lista jobs programados del runtime."""
    return web.json_response({"items": summarize_jobs(req.app["runtime_store"])})


async def desktop_jobs_post_handler(req: web.Request) -> web.Response:
    """Crea o actualiza un job programado."""
    payload = await req.json()
    try:
        job = req.app["runtime_store"].upsert_job(payload)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response(job.__dict__)


async def desktop_job_run_handler(req: web.Request) -> web.Response:
    """Ejecuta un job existente de forma manual."""
    job_id = req.match_info.get("job_id", "")
    try:
        result = await req.app["scheduler"].run_job_now(job_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=404)
    return web.json_response(result)


async def desktop_job_delete_handler(req: web.Request) -> web.Response:
    """Elimina un job del scheduler local."""
    job_id = req.match_info.get("job_id", "")
    req.app["runtime_store"].delete_job(job_id)
    return web.json_response({"deleted": True, "job_id": job_id})


def register_desktop_routes(app: web.Application) -> None:
    """Registra endpoints consumidos por la app desktop."""
    app.router.add_get("/api/health", health_handler)
    app.router.add_post("/api/desktop/chat", desktop_chat_handler)
    app.router.add_post("/api/desktop/chat/stream", desktop_chat_stream_handler)
    app.router.add_get("/api/desktop/processes", desktop_processes_handler)
    app.router.add_get("/api/desktop/memory", desktop_memory_handler)
    app.router.add_get("/api/desktop/workspace", desktop_workspace_handler)
    app.router.add_get("/api/desktop/hatching", desktop_hatching_get_handler)
    app.router.add_put("/api/desktop/hatching", desktop_hatching_put_handler)
    app.router.add_get("/api/desktop/runtime", desktop_runtime_get_handler)
    app.router.add_put("/api/desktop/runtime", desktop_runtime_put_handler)
    app.router.add_get("/api/desktop/jobs", desktop_jobs_get_handler)
    app.router.add_post("/api/desktop/jobs", desktop_jobs_post_handler)
    app.router.add_post("/api/desktop/jobs/{job_id}/run", desktop_job_run_handler)
    app.router.add_delete("/api/desktop/jobs/{job_id}", desktop_job_delete_handler)
