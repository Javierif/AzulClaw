# Telegram Deployment Notes

Deploy the relay Function from `src/relay_function` and configure Azure Bot
Service to call the Function's `/api/messages` endpoint.

The relay uses Service Bus to communicate with the local AzulClaw runtime.
