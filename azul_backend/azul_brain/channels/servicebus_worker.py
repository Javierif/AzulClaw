"""Service Bus consumer for processing incoming bot activities."""

import asyncio
import json
import logging

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient

from .proactive_sender import send_proactive_reply

LOGGER = logging.getLogger(__name__)

WELCOME_TEXT = "AzulClaw est\u00e1 activo. Dime qu\u00e9 necesitas."
EMPTY_TEXT_PROMPT = "Te escucho. Puedes decirme qu\u00e9 necesitas."
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
        use_sessions: str = "true",
        sync_reply_timeout_seconds: float = 6.8,
    ):
        self.orchestrator = orchestrator
        self.adapter = adapter
        self.connection_str = connection_str
        self.inbound_queue = inbound_queue
        self.outbound_queue = outbound_queue
        self.sync_reply_timeout_seconds = sync_reply_timeout_seconds
        raw_mode = (use_sessions or "auto").strip().lower()
        self.use_sessions = raw_mode if raw_mode in {"auto", "true", "false"} else "auto"

        self.client: ServiceBusClient | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()

    def _require_outbound_sessions(self, correlation_id: str) -> None:
        """Ensures synchronous request/reply uses isolated Service Bus sessions."""
        if self.use_sessions == "false":
            raise RuntimeError(
                "Synchronous outbound reply handling requires Azure Service Bus sessions "
                f"to be enabled on queue '{self.outbound_queue}' and SERVICE_BUS_USE_SESSIONS "
                f"must not be 'false' (correlation_id={correlation_id})."
            )

    async def _enqueue_sync_reply(self, text: str, correlation_id: str) -> None:
        """Stores the sync reply in the outbound queue for the Azure Function."""
        if not correlation_id:
            raise ValueError("Missing correlation_id for sync reply.")
        if self.client is None:
            raise RuntimeError("Service Bus client is not available for outbound sync reply.")

        self._require_outbound_sessions(correlation_id)

        payload = json.dumps(
            {
                "type": "message",
                "text": text,
                "speak": text,
            },
            ensure_ascii=False,
        )
        sender = self.client.get_queue_sender(queue_name=self.outbound_queue)
        async with sender:
            await sender.send_messages(
                ServiceBusMessage(
                    payload,
                    content_type="application/json",
                    correlation_id=correlation_id,
                    session_id=correlation_id,
                )
            )

        LOGGER.info(
            "[Worker] Sync reply queued on %s for %s (sessions=true).",
            self.outbound_queue,
            correlation_id,
        )

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
            raise ValueError("Malformed inbound activity payload.") from error

        correlation_id = msg.correlation_id or msg.session_id or ""
        if await self._handle_non_message_activity(activity, correlation_id):
            return

        text = (activity.get("text") or "").strip()
        user_id = activity.get("from", {}).get("id", "anonymous")

        if not text:
            LOGGER.info("[Worker] Received empty-text message from %s.", user_id)
            await self._send_sync_reply(activity, EMPTY_TEXT_PROMPT, correlation_id)
            return

        route = self.orchestrator.resolve_route(text)
        LOGGER.info(
            "[Worker] Processing channel activity from %s (correlation_id=%s, text_length=%d, lane=%s).",
            user_id,
            correlation_id,
            len(text),
            route.lane,
        )

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
                        timeout=self.sync_reply_timeout_seconds,
                    )
                    await self._send_sync_reply(activity, reply.text, correlation_id)
                except asyncio.TimeoutError:
                    timeout_reply = await self._build_slow_timeout_reply(text, route.reason)
                    await self._send_sync_reply(activity, timeout_reply, correlation_id)
                    self._track_background_task(
                        asyncio.create_task(self._finish_slow_follow_up(activity, slow_task))
                    )
            else:
                fast_task = asyncio.create_task(
                    self.orchestrator.process_user_message(
                        user_id=user_id,
                        user_message=text,
                        lane="fast",
                    )
                )
                try:
                    reply = await asyncio.wait_for(
                        asyncio.shield(fast_task),
                        timeout=self.sync_reply_timeout_seconds,
                    )
                    await self._send_sync_reply(activity, reply.text, correlation_id)
                except asyncio.TimeoutError:
                    timeout_reply = await self._build_slow_timeout_reply(text, route.reason)
                    await self._send_sync_reply(activity, timeout_reply, correlation_id)
                    self._track_background_task(
                        asyncio.create_task(self._finish_slow_follow_up(activity, fast_task))
                    )

        except Exception as error:
            LOGGER.error("[Worker] Error in AI pipeline: %s", error)
            await self._send_sync_reply(activity, GENERIC_ERROR_TEXT, correlation_id)

    async def _process_inbound_message(
        self,
        receiver,
        msg,
        semaphore: asyncio.Semaphore,
    ) -> None:
        """Processes and settles one inbound message, then releases its concurrency slot."""
        try:
            await self._handle_message(msg)
            await receiver.complete_message(msg)
        except ValueError as error:
            LOGGER.warning("[Worker] Dead-lettering inbound message: %s", error)
            await receiver.dead_letter_message(
                msg,
                reason="MalformedInboundActivity",
                error_description=str(error),
            )
        except Exception:
            await receiver.abandon_message(msg)
            raise
        finally:
            semaphore.release()

    async def _listen_loop(self) -> None:
        """Main listening loop."""
        max_concurrent_calls = 5
        while self._running:
            try:
                self.client = ServiceBusClient.from_connection_string(self.connection_str)
                async with self.client:
                    receiver = self.client.get_queue_receiver(
                        queue_name=self.inbound_queue,
                        prefetch_count=max_concurrent_calls,
                    )
                    async with receiver:
                        LOGGER.info("[Worker] Service Bus inbound connection ESTABLISHED.")
                        semaphore = asyncio.Semaphore(max_concurrent_calls)
                        in_flight_tasks: set[asyncio.Task] = set()
                        fatal_error: Exception | None = None

                        def _handle_task_done(task: asyncio.Task) -> None:
                            nonlocal fatal_error
                            in_flight_tasks.discard(task)
                            try:
                                exception = task.exception()
                            except asyncio.CancelledError:
                                return
                            if exception is not None and fatal_error is None:
                                fatal_error = exception

                        try:
                            while self._running:
                                if fatal_error is not None:
                                    raise fatal_error

                                messages = await receiver.receive_messages(
                                    max_wait_time=5,
                                    max_message_count=max_concurrent_calls,
                                )
                                for msg in messages:
                                    if fatal_error is not None:
                                        raise fatal_error

                                    await semaphore.acquire()
                                    if fatal_error is not None:
                                        semaphore.release()
                                        raise fatal_error

                                    task = asyncio.create_task(
                                        self._process_inbound_message(receiver, msg, semaphore)
                                    )
                                    in_flight_tasks.add(task)
                                    task.add_done_callback(_handle_task_done)
                        finally:
                            if in_flight_tasks:
                                if not self._running:
                                    for task in list(in_flight_tasks):
                                        task.cancel()
                                results = await asyncio.gather(*in_flight_tasks, return_exceptions=True)
                                if fatal_error is None:
                                    for result in results:
                                        if isinstance(result, Exception) and not isinstance(
                                            result, asyncio.CancelledError
                                        ):
                                            fatal_error = result
                                            break
                            if fatal_error is not None:
                                raise fatal_error
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
