import json
import logging
import os
import uuid

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

CONNECTION_STR = os.getenv("SERVICE_BUS_CONNECTION_STRING", "")
INBOUND_QUEUE = os.getenv("SERVICE_BUS_INBOUND_QUEUE", "bot-inbound")


def build_reply_activity(text: str, speak: str | None = None) -> dict:
    return {
        "type": "message",
        "text": text,
        "speak": speak or text,
        "inputHint": "acceptingInput",
    }


def build_expected_replies(activity: dict) -> dict:
    return {"activities": [activity]}


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hola mundo", status_code=200)


@app.route(route="api/messages", methods=["POST"])
async def messages(req: func.HttpRequest) -> func.HttpResponse:
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
        channel_id,
        activity_type or "<empty>",
        delivery_mode or "<empty>",
        text[:80],
    )

    try:
        if CONNECTION_STR:
            from azure.servicebus import ServiceBusMessage
            from azure.servicebus.aio import ServiceBusClient

            async with ServiceBusClient.from_connection_string(CONNECTION_STR) as client:
                sender = client.get_queue_sender(queue_name=INBOUND_QUEUE)
                sb_msg = ServiceBusMessage(
                    json.dumps(req_body),
                    correlation_id=correlation_id,
                    content_type="application/json",
                )
                async with sender:
                    await sender.send_messages(sb_msg)
                logging.info("Enqueued %s", correlation_id)
    except Exception as error:
        logging.error("SB Error for %s: %s", correlation_id, error)

    if activity_type == "conversationUpdate":
        reply = build_reply_activity(
            text="AzulClaw esta activo. Dime que necesitas.",
            speak="AzulClaw está activo. Dime qué necesitas.",
        )
    elif text:
        reply = build_reply_activity(
            text="AzulClaw está procesando tu petición.",
            speak="Vale, lo miro ahora mismo.",
        )
    else:
        reply = build_reply_activity(
            text="Te escucho.",
            speak="Te escucho.",
        )

    if delivery_mode == "expectReplies":
        body = build_expected_replies(reply)
        logging.info("Returning ExpectedReplies for %s", correlation_id)
        return func.HttpResponse(
            body=json.dumps(body, ensure_ascii=False),
            status_code=200,
            mimetype="application/json",
        )

    logging.info("Returning single activity body for %s", correlation_id)
    return func.HttpResponse(
        body=json.dumps(reply, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
    )
