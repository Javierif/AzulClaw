import azure.functions as func
import logging
import json
import uuid
import os

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

CONNECTION_STR = os.getenv("SERVICE_BUS_CONNECTION_STRING", "")
INBOUND_QUEUE = os.getenv("SERVICE_BUS_INBOUND_QUEUE", "bot-inbound")

@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Viva", status_code=200)

@app.route(route="api/messages", methods=["POST"])
async def messages(req: func.HttpRequest) -> func.HttpResponse:
    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON", status_code=400)

    correlation_id = str(uuid.uuid4())

    # 1. Intentar encolar en Service Bus
    try:
        if CONNECTION_STR:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus import ServiceBusMessage
            async with ServiceBusClient.from_connection_string(CONNECTION_STR) as client:
                sender = client.get_queue_sender(queue_name=INBOUND_QUEUE)
                sb_msg = ServiceBusMessage(
                    json.dumps(req_body), 
                    correlation_id=correlation_id,
                    content_type="application/json"
                )
                async with sender:
                    await sender.send_messages(sb_msg)
                logging.info(f"Enqueued {correlation_id}")
    except Exception as e:
        logging.error(f"SB Error: {e}")

    # 2. Devolver una respuesta inmediata para que Alexa no se quede muda
    # Construimos un objeto Activity de respuesta síncrona básico
    response_activity = {
        "type": "message",
        "text": "AzulClaw está procesando tu petición...",
        "speak": "Vale, lo miro ahora mismo.",
        "inputHint": "acceptingInput"
    }

    return func.HttpResponse(
        body=json.dumps(response_activity),
        status_code=200,
        mimetype="application/json"
    )
