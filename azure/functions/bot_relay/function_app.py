import asyncio
import json
import logging
import os
import uuid

import azure.functions as func
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

CONNECTION_STR = os.getenv("SERVICE_BUS_CONNECTION_STRING", "")
INBOUND_QUEUE = os.getenv("SERVICE_BUS_INBOUND_QUEUE", "bot-inbound")
OUTBOUND_QUEUE = os.getenv("SERVICE_BUS_OUTBOUND_QUEUE", "bot-outbound")
USE_SESSIONS = (os.getenv("SERVICE_BUS_USE_SESSIONS", "auto") or "auto").strip().lower()
SYNC_REPLY_TIMEOUT_SECONDS = float(os.getenv("BOT_SYNC_REPLY_TIMEOUT_SECONDS", "6.8"))


def _message_body_to_text(msg) -> str:
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


def _fallback_reply(activity_type: str, text: str) -> dict:
    if activity_type == "conversationUpdate":
        return {
            "type": "message",
            "text": "AzulClaw esta activo. Dime que necesitas.",
            "speak": "AzulClaw está activo. Dime qué necesitas.",
            "inputHint": "acceptingInput",
        }
    if text:
        return {
            "type": "message",
            "text": "AzulClaw está procesando tu petición.",
            "speak": "Vale, lo miro ahora mismo.",
            "inputHint": "acceptingInput",
        }
    return {
        "type": "message",
        "text": "Te escucho.",
        "speak": "Te escucho.",
        "inputHint": "acceptingInput",
    }


def _build_http_body(reply: dict, delivery_mode: str) -> str:
    if delivery_mode == "expectReplies":
        return json.dumps({"activities": [reply]}, ensure_ascii=False)
    return json.dumps(reply, ensure_ascii=False)


async def _await_outbound_with_sessions(client: ServiceBusClient, correlation_id: str) -> dict | None:
    receiver = client.get_queue_receiver(
        queue_name=OUTBOUND_QUEUE,
        session_id=correlation_id,
        max_wait_time=SYNC_REPLY_TIMEOUT_SECONDS,
        prefetch_count=1,
    )
    async with receiver:
        messages = await receiver.receive_messages(
            max_wait_time=SYNC_REPLY_TIMEOUT_SECONDS,
            max_message_count=1,
        )
        if not messages:
            return None

        outbound_msg = messages[0]
        payload = json.loads(_message_body_to_text(outbound_msg))
        await receiver.complete_message(outbound_msg)
        return payload


async def _await_outbound_without_sessions(client: ServiceBusClient, correlation_id: str) -> dict | None:
    receiver = client.get_queue_receiver(
        queue_name=OUTBOUND_QUEUE,
        max_wait_time=1,
        prefetch_count=1,
    )
    deadline = asyncio.get_running_loop().time() + SYNC_REPLY_TIMEOUT_SECONDS

    async with receiver:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None

            messages = await receiver.receive_messages(
                max_wait_time=min(remaining, 1),
                max_message_count=1,
            )
            if not messages:
                continue

            outbound_msg = messages[0]
            if (outbound_msg.correlation_id or "") != correlation_id:
                await receiver.abandon_message(outbound_msg)
                continue

            payload = json.loads(_message_body_to_text(outbound_msg))
            await receiver.complete_message(outbound_msg)
            return payload


async def _await_outbound_reply(client: ServiceBusClient, correlation_id: str) -> dict | None:
    if USE_SESSIONS != "false":
        try:
            return await _await_outbound_with_sessions(client, correlation_id)
        except Exception as error:
            if USE_SESSIONS == "true":
                raise
            logging.warning(
                "Session receive failed for %s, retrying without sessions: %s",
                correlation_id,
                error,
            )
    return await _await_outbound_without_sessions(client, correlation_id)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hola mundo", status_code=200)


@app.route(route="api/messages", methods=["POST"])
async def messages(req: func.HttpRequest) -> func.HttpResponse:
    if not CONNECTION_STR:
        return func.HttpResponse("SERVICE_BUS_CONNECTION_STRING is not configured.", status_code=500)

    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400)

    correlation_id = str(uuid.uuid4())
    delivery_mode = (req_body.get("deliveryMode") or "").strip()
    activity_type = (req_body.get("type") or "").strip()
    channel_id = (req_body.get("channelId") or "").strip()
    text = (req_body.get("text") or "").strip()

    logging.info(
        "Incoming activity correlation=%s channel=%s type=%s deliveryMode=%s text=%s",
        correlation_id,
        channel_id or "<empty>",
        activity_type or "<empty>",
        delivery_mode or "<empty>",
        text[:80],
    )

    try:
        async with ServiceBusClient.from_connection_string(CONNECTION_STR) as client:
            sender = client.get_queue_sender(queue_name=INBOUND_QUEUE)
            async with sender:
                await sender.send_messages(
                    ServiceBusMessage(
                        json.dumps(req_body),
                        correlation_id=correlation_id,
                        session_id=correlation_id if USE_SESSIONS != "false" else None,
                        content_type="application/json",
                    )
                )
            logging.info("Enqueued %s to %s", correlation_id, INBOUND_QUEUE)

            reply = await _await_outbound_reply(client, correlation_id)
    except Exception as error:
        logging.error("Relay error for %s: %s", correlation_id, error)
        reply = None

    if reply is None:
        logging.warning("No sync reply received for %s before timeout.", correlation_id)
        reply = _fallback_reply(activity_type, text)
    else:
        reply.setdefault("inputHint", "acceptingInput")
        logging.info("Sync reply received for %s: %s", correlation_id, reply.get("text", "")[:120])

    return func.HttpResponse(
        body=_build_http_body(reply, delivery_mode),
        status_code=200,
        mimetype="application/json",
    )
