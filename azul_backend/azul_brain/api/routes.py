"""HTTP routes for the desktop app."""

from __future__ import annotations

import json
import logging

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
    wipe_local_user_data,
)

LOGGER = logging.getLogger(__name__)


def _desktop_user_id(value: object = "desktop-user") -> str:
    return str(value or "desktop-user").strip() or "desktop-user"


async def health_handler(_: web.Request) -> web.Response:
    """Returns basic health status of the local backend."""
    return web.json_response({"status": "ok"})


async def desktop_chat_handler(req: web.Request) -> web.Response:
    """Processes a message from the desktop app."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = _desktop_user_id(payload.get("user_id"))
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
    """Processes a message from the desktop app and emits incremental NDJSON."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()

    user_id = _desktop_user_id(payload.get("user_id"))
    message = str(payload.get("message", "")).strip()
    conversation_id = str(payload.get("conversation_id", "")).strip() or None

    # If no conversation supplied, get or create an empty one so messages are always scoped
    if not conversation_id:
        conversation_id, _ = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conversation_id)

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
            conversation_id=conversation_id,
            on_delta=lambda text: write_event({"type": "delta", "text": text}),
            on_commentary=lambda text: write_event({"type": "commentary", "text": text}),
            on_progress=lambda progress: write_event({"type": "progress", "progress": progress}),
        )
        history = orchestrator.memory.get_conversation_messages(conversation_id, limit=12)
        conv_title = reply.conversation_title or orchestrator.memory.get_conversation_title(
            conversation_id
        )
        await write_event(
            {
                "type": "done",
                "reply": reply.text,
                "history": history,
                "conversation_id": conversation_id,
                "conversation_title": conv_title or "",
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


async def desktop_conversations_handler(req: web.Request) -> web.Response:
    """Lists conversations for the desktop user."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id"))
    convs = orchestrator.memory.list_conversations(user_id)
    return web.json_response({"items": convs})


async def desktop_create_conversation_handler(req: web.Request) -> web.Response:
    """Returns an existing empty conversation or creates one (idempotent)."""
    orchestrator = req.app["orchestrator"]
    payload = await req.json()
    user_id = _desktop_user_id(payload.get("user_id"))
    conv_id, title = orchestrator.memory.get_or_create_empty_conversation(user_id)
    orchestrator.memory.set_active_conversation(user_id, conv_id)
    return web.json_response({"id": conv_id, "title": title})


async def desktop_conversation_messages_handler(req: web.Request) -> web.Response:
    """Returns messages for a specific conversation."""
    orchestrator = req.app["orchestrator"]
    conv_id = req.match_info.get("conv_id", "").strip()
    user_id = _desktop_user_id(req.query.get("user_id"))
    if not conv_id:
        return web.json_response({"error": "conv_id required"}, status=400)
    orchestrator.memory.set_active_conversation(user_id, conv_id)
    msgs = orchestrator.memory.get_conversation_messages(conv_id)
    return web.json_response({"messages": msgs})


async def desktop_delete_conversation_handler(req: web.Request) -> web.Response:
    """Deletes a conversation and all its messages."""
    orchestrator = req.app["orchestrator"]
    conv_id = req.match_info.get("conv_id", "").strip()
    if not conv_id:
        return web.json_response({"error": "conv_id required"}, status=400)
    deleted = orchestrator.memory.delete_conversation(conv_id)
    if not deleted:
        return web.json_response({"error": "Conversation not found"}, status=404)
    return web.json_response({"deleted": True, "id": conv_id})


async def desktop_processes_handler(_: web.Request) -> web.Response:
    """Returns the process summary visible to the desktop app."""
    return web.json_response({"items": summarize_processes(_.app["process_registry"])})


async def desktop_memory_handler(req: web.Request) -> web.Response:
    """Returns a summarised memory view for the desktop app."""
    orchestrator = req.app["orchestrator"]
    user_id = _desktop_user_id(req.query.get("user_id"))
    return web.json_response({"items": summarize_memory(orchestrator, user_id)})


async def desktop_memory_delete_handler(req: web.Request) -> web.Response:
    """Deletes a specific memory entry from the vector store."""
    memory_id = req.match_info.get("memory_id", "").strip()
    user_id = _desktop_user_id(req.query.get("user_id"))

    if not memory_id:
        return web.json_response({"error": "memory_id required"}, status=400)

    orchestrator = req.app.get("orchestrator")
    vector_memory = getattr(orchestrator, "vector_memory", None) if orchestrator else None
    if vector_memory is None:
        return web.json_response({"error": "Vector memory unavailable"}, status=503)

    deleted = vector_memory.delete_memory(memory_id, user_id)
    if not deleted:
        return web.json_response({"error": "Memory not found"}, status=404)

    return web.json_response({"deleted": True, "id": memory_id})


async def desktop_workspace_handler(req: web.Request) -> web.Response:
    """Lists the contents of the sandbox workspace."""
    relative_path = req.query.get("path", ".")
    try:
        listing = list_workspace_entries(relative_path)
    except Exception as error:
        return web.json_response({"error": str(error)}, status=400)

    return web.json_response(listing)


async def desktop_hatching_get_handler(_: web.Request) -> web.Response:
    """Returns the current Hatching profile."""
    return web.json_response(load_hatching_profile())


async def desktop_hatching_put_handler(req: web.Request) -> web.Response:
    """Saves the Hatching profile sent by the desktop app."""
    import asyncio
    payload = await req.json()
    result = save_hatching_profile(payload)
    orchestrator = req.app.get("orchestrator")
    if orchestrator is not None and hasattr(orchestrator, "reload_persistent_memory"):
        try:
            orchestrator.reload_persistent_memory()
        except Exception as error:
            LOGGER.warning("[Memory] reload after hatching save failed: %s", error)
        # Seed profile facts when the user completes or re-saves onboarding
        if result.get("is_hatched") and hasattr(orchestrator, "seed_profile_facts"):
            asyncio.create_task(orchestrator.seed_profile_facts())
    return web.json_response(result)


async def desktop_data_wipe_handler(req: web.Request) -> web.Response:
    """Clears SQLite memory and resets hatching (requires brain restart)."""
    try:
        payload = await req.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "JSON body required"}, status=400)

    try:
        result = wipe_local_user_data(str(payload.get("confirm", "")))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    orchestrator = req.app.get("orchestrator")
    if orchestrator is not None and hasattr(orchestrator, "reload_persistent_memory"):
        try:
            orchestrator.reload_persistent_memory()
        except Exception as error:
            LOGGER.warning("[Memory] reload after data wipe failed: %s", error)

    return web.json_response(result)


async def desktop_runtime_get_handler(req: web.Request) -> web.Response:
    """Returns aggregated status of the local runtime."""
    return web.json_response(
        summarize_runtime(
            req.app["runtime_manager"],
            req.app["scheduler"],
            req.app["process_registry"],
        )
    )


async def desktop_runtime_put_handler(req: web.Request) -> web.Response:
    """Saves editable runtime configuration."""
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
    """Lists scheduled runtime jobs."""
    return web.json_response({"items": summarize_jobs(req.app["runtime_store"])})


async def desktop_jobs_post_handler(req: web.Request) -> web.Response:
    """Creates or updates a scheduled job."""
    payload = await req.json()
    try:
        job = req.app["runtime_store"].upsert_job(payload)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response(job.__dict__)


async def desktop_job_run_handler(req: web.Request) -> web.Response:
    """Runs an existing job manually."""
    job_id = req.match_info.get("job_id", "")
    try:
        result = await req.app["scheduler"].run_job_now(job_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=404)
    return web.json_response(result)


async def desktop_job_delete_handler(req: web.Request) -> web.Response:
    """Deletes a job from the local scheduler."""
    job_id = req.match_info.get("job_id", "")
    try:
        req.app["runtime_store"].delete_job(job_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response({"deleted": True, "job_id": job_id})


def register_desktop_routes(app: web.Application) -> None:
    """Registers endpoints consumed by the desktop app."""
    app.router.add_get("/api/health", health_handler)
    app.router.add_post("/api/desktop/chat", desktop_chat_handler)
    app.router.add_post("/api/desktop/chat/stream", desktop_chat_stream_handler)
    app.router.add_get("/api/desktop/conversations", desktop_conversations_handler)
    app.router.add_post("/api/desktop/conversations", desktop_create_conversation_handler)
    app.router.add_get("/api/desktop/conversations/{conv_id}/messages", desktop_conversation_messages_handler)
    app.router.add_delete("/api/desktop/conversations/{conv_id}", desktop_delete_conversation_handler)
    app.router.add_get("/api/desktop/processes", desktop_processes_handler)
    app.router.add_get("/api/desktop/memory", desktop_memory_handler)
    app.router.add_delete("/api/desktop/memory/{memory_id}", desktop_memory_delete_handler)
    app.router.add_get("/api/desktop/workspace", desktop_workspace_handler)
    app.router.add_get("/api/desktop/hatching", desktop_hatching_get_handler)
    app.router.add_put("/api/desktop/hatching", desktop_hatching_put_handler)
    app.router.add_post("/api/desktop/data-wipe", desktop_data_wipe_handler)
    app.router.add_get("/api/desktop/runtime", desktop_runtime_get_handler)
    app.router.add_put("/api/desktop/runtime", desktop_runtime_put_handler)
    app.router.add_get("/api/desktop/jobs", desktop_jobs_get_handler)
    app.router.add_post("/api/desktop/jobs", desktop_jobs_post_handler)
    app.router.add_post("/api/desktop/jobs/{job_id}/run", desktop_job_run_handler)
    app.router.add_delete("/api/desktop/jobs/{job_id}", desktop_job_delete_handler)
