"""Service Bus consumer for processing incoming bot activities."""

import asyncio
import json
import logging

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient

from .proactive_sender import send_proactive_reply

LOGGER = logging.getLogger(__name__)

SYNC_REPLY_TIMEOUT_SECONDS = 5.0
WELCOME_TEXT = "AzulClaw esta activo. Dime que necesitas."
EMPTY_TEXT_PROMPT = "Te escucho. Puedes decirme que necesitas."
GENERIC_ERROR_TEXT = "Ha ocurrido un error en mi cerebro."
SLOW_TIMEOUT_TEXT = "Estoy preparando la respuesta. Vuelve a intentarlo en unos segundos."


def _message_body_to_text(msg) -> str:
    """Normalises a Service Bus payload into plain text."""
    body = getattr(msg, "body", None)
    if body is None:
        return str(msg)
    if isinstance(body, bytes):
        return body.decode("utf-8")

    chunks: list[bytes] = []
    try:
        for part in body:
            if isinstance(part, bytes):
                chunks.append(part)
            else:
                chunks.append(str(part).encode("utf-8"))
    except TypeError:
        return str(body)

    return b"".join(chunks).decode("utf-8")


class ServiceBusWorker:
    """Consumes Azure Bot activities from Service Bus and bridges them to the local orchestrator."""

    def __init__(
        self,
        orchestrator,
        adapter,
        connection_str: str,
        inbound_queue: str,
        outbound_queue: str,
        use_sessions: str = "auto",
    ):
        self.orchestrator = orchestrator
        self.adapter = adapter
        self.connection_str = connection_str
        self.inbound_queue = inbound_queue
        self.outbound_queue = outbound_queue
        raw_mode = (use_sessions or "auto").strip().lower()
        self.use_sessions = raw_mode if raw_mode in {"auto", "true", "false"} else "auto"

        self.client: ServiceBusClient | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()

    async def _enqueue_sync_reply(self, text: str, correlation_id: str) -> None:
        """Stores the sync reply in the outbound queue for the Azure Function."""
        if not correlation_id:
            raise ValueError("Missing correlation_id for sync reply.")

        payload = json.dumps(
            {
                "type": "message",
                "text": text,
                "speak": text,
            },
            ensure_ascii=False,
        )

        session_attempts = [True, False] if self.use_sessions == "auto" else [self.use_sessions == "true"]
        last_error: Exception | None = None

        for use_sessions in session_attempts:
            try:
                message_kwargs = {
                    "content_type": "application/json",
                    "correlation_id": correlation_id,
                }
                if use_sessions:
                    message_kwargs["session_id"] = correlation_id

                async with ServiceBusClient.from_connection_string(self.connection_str) as client:
                    sender = client.get_queue_sender(queue_name=self.outbound_queue)
                    async with sender:
                        await sender.send_messages(ServiceBusMessage(payload, **message_kwargs))

                LOGGER.info(
                    "[Worker] Sync reply queued on %s for %s (sessions=%s).",
                    self.outbound_queue,
                    correlation_id,
                    use_sessions,
                )
                return
            except Exception as error:
                last_error = error
                if self.use_sessions == "auto" and use_sessions:
                    LOGGER.warning(
                        "[Worker] Outbound session send failed for %s, retrying without sessions: %s",
                        correlation_id,
                        error,
                    )
                    continue
                raise

        if last_error is not None:
            raise last_error

    async def _send_sync_reply(self, original_activity: dict, text: str, correlation_id: str) -> None:
        """Pushes the sync response to the outbound queue consumed by the Azure Function."""
        try:
            await self._enqueue_sync_reply(text, correlation_id)
        except Exception as error:
            LOGGER.error("[Worker] Failed to enqueue sync reply: %s", error)
            try:
                await send_proactive_reply(self.adapter, original_activity, text)
                LOGGER.info("[Worker] Fallback proactive reply sent via Service Url.")
            except Exception as proactive_error:
                LOGGER.error("[Worker] Proactive fallback also failed: %s", proactive_error)

    async def _build_slow_timeout_reply(self, text: str, reason: str) -> str:
        """Builds a short spoken acknowledgement if the slow answer exceeds the sync SLA."""
        try:
            return await self.orchestrator.generate_fast_visible_commentary(
                text,
                reason=reason,
                lane="slow",
            )
        except Exception as error:
            LOGGER.warning("[Worker] Could not build slow timeout commentary: %s", error)
            return SLOW_TIMEOUT_TEXT

    def _track_background_task(self, task: asyncio.Task) -> None:
        """Keeps detached follow-up tasks alive and logs failures."""
        self._background_tasks.add(task)

        def _on_done(done_task: asyncio.Task) -> None:
            self._background_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as error:
                LOGGER.error("[Worker] Background task failed: %s", error)

        task.add_done_callback(_on_done)

    async def _finish_slow_follow_up(self, activity: dict, slow_task: asyncio.Task) -> None:
        """Sends the late proactive reply after the inbound message has been completed."""
        final_reply = await slow_task
        if activity.get("channelId") == "alexa":
            LOGGER.info("[Worker] Skipping late proactive reply for Alexa after timeout.")
            return

        await send_proactive_reply(self.adapter, activity, final_reply.text)

    async def _handle_non_message_activity(self, activity: dict, correlation_id: str) -> bool:
        """Answers channel open events so voice channels never receive an empty payload."""
        activity_type = (activity.get("type") or "").strip()

        if activity_type == "message":
            return False

        if activity_type in {"conversationUpdate", "contactRelationUpdate", "event"}:
            LOGGER.info("[Worker] Handling %s with welcome prompt.", activity_type)
            await self._send_sync_reply(activity, WELCOME_TEXT, correlation_id)
            return True

        LOGGER.info("[Worker] Ignoring unsupported activity type: %s", activity_type)
        return True

    async def _handle_message(self, msg) -> None:
        """Processes a single activity via orchestrator and responds."""
        try:
            body = _message_body_to_text(msg)
            activity = json.loads(body)
        except Exception as error:
            LOGGER.error("[Worker] Failed to parse Service Bus message: %s", error)
            return

        correlation_id = msg.correlation_id or msg.session_id or ""
        if await self._handle_non_message_activity(activity, correlation_id):
            return

        text = (activity.get("text") or "").strip()
        user_id = activity.get("from", {}).get("id", "anonymous")

        if not text:
            LOGGER.info("[Worker] Received empty-text message from %s.", user_id)
            await self._send_sync_reply(activity, EMPTY_TEXT_PROMPT, correlation_id)
            return

        LOGGER.info("[Worker] Processing channel activity from %s: %s...", user_id, text[:50])

        route = self.orchestrator.resolve_route(text)

        try:
            if route.lane == "slow":
                slow_task = asyncio.create_task(
                    self.orchestrator.process_user_message(
                        user_id=user_id,
                        user_message=text,
                        lane="slow",
                    )
                )
                try:
                    reply = await asyncio.wait_for(
                        asyncio.shield(slow_task),
                        timeout=SYNC_REPLY_TIMEOUT_SECONDS,
                    )
                    await self._send_sync_reply(activity, reply.text, correlation_id)
                except asyncio.TimeoutError:
                    timeout_reply = await self._build_slow_timeout_reply(text, route.reason)
                    await self._send_sync_reply(activity, timeout_reply, correlation_id)
                    self._track_background_task(
                        asyncio.create_task(self._finish_slow_follow_up(activity, slow_task))
                    )
            else:
                reply = await self.orchestrator.process_user_message(
                    user_id=user_id,
                    user_message=text,
                    lane="fast",
                )
                await self._send_sync_reply(activity, reply.text, correlation_id)

        except Exception as error:
            LOGGER.error("[Worker] Error in AI pipeline: %s", error)
            await self._send_sync_reply(activity, GENERIC_ERROR_TEXT, correlation_id)

    async def _listen_loop(self) -> None:
        """Main listening loop."""
        while self._running:
            try:
                self.client = ServiceBusClient.from_connection_string(self.connection_str)
                async with self.client:
                    receiver = self.client.get_queue_receiver(
                        queue_name=self.inbound_queue,
                        prefetch_count=5,
                    )
                    async with receiver:
                        LOGGER.info("[Worker] Service Bus inbound connection ESTABLISHED.")
                        while self._running:
                            messages = await receiver.receive_messages(max_wait_time=5, max_message_count=5)
                            for msg in messages:
                                try:
                                    await self._handle_message(msg)
                                    await receiver.complete_message(msg)
                                except Exception:
                                    await receiver.abandon_message(msg)
                                    raise
            except asyncio.CancelledError:
                break
            except Exception as error:
                LOGGER.error("[Worker] Connection lost, retrying in 5s: %s", error)
                if self._running:
                    await asyncio.sleep(5)

    async def start(self) -> None:
        """Starts the background worker task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        LOGGER.info("[Worker] Service Bus watcher STARTED.")

    async def stop(self) -> None:
        """Stops the worker task cleanly."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for task in list(self._background_tasks):
            task.cancel()
        for task in list(self._background_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.close()
        LOGGER.info("[Worker] Service Bus watcher STOPPED.")
