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
USE_SESSIONS = (os.getenv("SERVICE_BUS_USE_SESSIONS", "true") or "true").strip().lower()
REQUIRE_AUTH = (os.getenv("BOT_RELAY_REQUIRE_AUTH", "true") or "true").strip().lower() != "false"
SYNC_REPLY_TIMEOUT_SECONDS = float(os.getenv("BOT_SYNC_REPLY_TIMEOUT_SECONDS", "6.8"))
APP_ID = os.getenv("MicrosoftAppId", "")
APP_PASSWORD = os.getenv("MicrosoftAppPassword", "")
_SERVICEBUS_CLIENT: ServiceBusClient | None = None


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
            "text": "AzulClaw est\u00e1 activo. Dime qu\u00e9 necesitas.",
            "speak": "AzulClaw est\u00e1 activo. Dime qu\u00e9 necesitas.",
            "inputHint": "acceptingInput",
        }
    if text:
        return {
            "type": "message",
            "text": "AzulClaw est\u00e1 procesando tu petici\u00f3n.",
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


def _should_wait_for_sync_reply(channel_id: str, delivery_mode: str) -> bool:
    """Returns whether the relay should wait for a synchronous reply body."""
    if delivery_mode == "expectReplies":
        return True
    return (channel_id or "").strip().lower() == "alexa"


def _get_servicebus_client() -> ServiceBusClient:
    global _SERVICEBUS_CLIENT
    if _SERVICEBUS_CLIENT is None:
        _SERVICEBUS_CLIENT = ServiceBusClient.from_connection_string(CONNECTION_STR)
    return _SERVICEBUS_CLIENT


def _raise_sessions_required(correlation_id: str) -> None:
    raise RuntimeError(
        "Synchronous outbound reply handling requires Azure Service Bus sessions "
        f"to be enabled on queue '{OUTBOUND_QUEUE}' and SERVICE_BUS_USE_SESSIONS "
        f"must not be 'false' (correlation_id={correlation_id})."
    )


async def _authenticate_request(req_body: dict, auth_header: str) -> tuple[bool, int, str]:
    if not REQUIRE_AUTH:
        return True, 200, ""

    if not APP_ID or not APP_PASSWORD:
        logging.error("Bot relay auth is enabled but Microsoft app credentials are incomplete.")
        return False, 500, "Bot relay authentication is not configured."

    if not auth_header:
        return False, 401, "Missing Authorization header."

    try:
        from botbuilder.schema import Activity
        from botframework.connector.auth.credential_provider import SimpleCredentialProvider
        from botframework.connector.auth.jwt_token_validation import JwtTokenValidation

        activity = Activity().deserialize(req_body)
        await JwtTokenValidation.authenticate_request(
            activity,
            auth_header,
            SimpleCredentialProvider(APP_ID, APP_PASSWORD),
        )
        return True, 200, ""
    except PermissionError:
        return False, 401, "Unauthorized."
    except ImportError as error:
        logging.error("Bot relay authentication dependencies failed to import: %s", error)
        return False, 500, "Bot relay authentication dependencies are unavailable."
    except Exception as error:
        logging.error("Bot relay authentication failed: %s", error)
        return False, 403, "Forbidden."


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
        body_text = _message_body_to_text(outbound_msg)
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as error:
            logging.error(
                "Invalid JSON in outbound message for correlation_id=%s on queue=%s: %s",
                correlation_id,
                OUTBOUND_QUEUE,
                error,
            )
            await receiver.dead_letter_message(
                outbound_msg,
                reason="InvalidJson",
                error_description=f"Outbound message body is not valid JSON: {error}",
            )
            return None

        await receiver.complete_message(outbound_msg)
        return payload


async def _await_outbound_reply(client: ServiceBusClient, correlation_id: str) -> dict | None:
    if USE_SESSIONS == "false":
        logging.info(
            "Skipping synchronous outbound wait for %s because SERVICE_BUS_USE_SESSIONS=false.",
            correlation_id,
        )
        return None

    try:
        return await _await_outbound_with_sessions(client, correlation_id)
    except Exception as error:
        error_text = str(error).lower()
        if (
            "session" in error_text
            and (
                "require" in error_text
                or "enabled" in error_text
                or "accept" in error_text
                or "disabled" in error_text
                or "sessionful" in error_text
            )
        ):
            _raise_sessions_required(correlation_id)

        logging.exception(
            "Session receive failed for %s on %s; returning no synchronous reply.",
            correlation_id,
            OUTBOUND_QUEUE,
        )
        return None


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
    auth_header = req.headers.get("Authorization", "")

    is_authorized, auth_status, auth_message = await _authenticate_request(req_body, auth_header)
    if not is_authorized:
        return func.HttpResponse(auth_message, status_code=auth_status)

    logging.info(
        "Incoming activity correlation=%s channel=%s type=%s deliveryMode=%s text_length=%s",
        correlation_id,
        channel_id or "<empty>",
        activity_type or "<empty>",
        delivery_mode or "<empty>",
        len(text),
    )

    try:
        client = _get_servicebus_client()
        sender = client.get_queue_sender(queue_name=INBOUND_QUEUE)
        async with sender:
            await sender.send_messages(
                ServiceBusMessage(
                    json.dumps(req_body),
                    correlation_id=correlation_id,
                    content_type="application/json",
                )
            )
        logging.info("Enqueued %s to %s", correlation_id, INBOUND_QUEUE)

        if _should_wait_for_sync_reply(channel_id, delivery_mode):
            reply = await _await_outbound_reply(client, correlation_id)
        else:
            logging.info(
                "Skipping synchronous HTTP reply wait for %s on channel=%s deliveryMode=%s.",
                correlation_id,
                channel_id or "<empty>",
                delivery_mode or "<empty>",
            )
            reply = None
    except Exception as error:
        logging.error("Relay error for %s: %s", correlation_id, error)
        reply = None

    if reply is None and not _should_wait_for_sync_reply(channel_id, delivery_mode):
        return func.HttpResponse(status_code=200)

    if reply is None:
        logging.warning("No sync reply received for %s before timeout.", correlation_id)
        reply = _fallback_reply(activity_type, text)
    else:
        reply.setdefault("inputHint", "acceptingInput")
        logging.info(
            "Sync reply received for %s type=%s text_length=%s",
            correlation_id,
            reply.get("type", "<empty>"),
            len((reply.get("text") or "").strip()),
        )

    return func.HttpResponse(
        body=_build_http_body(reply, delivery_mode),
        status_code=200,
        mimetype="application/json",
    )
