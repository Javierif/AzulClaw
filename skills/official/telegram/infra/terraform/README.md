# Telegram Terraform

This folder owns Azure infrastructure needed only by the Telegram skill.

The `envs/dev` and `envs/prod` stacks create:

- Azure Function App for the relay
- Storage account for Functions
- Service Bus queues for inbound and outbound relay traffic
- Application Insights
- app settings for Bot Framework and Telegram allowlists
- optional Azure Bot resource when `bot_service_name` is set

The relay code is not zipped by Terraform. After `terraform apply`, deploy the
Python Function app from `skills/official/telegram/src/relay_function/`.

When `bot_service_name` is empty, manage the Azure Bot resource separately in
Azure Bot Service. When it is set, Terraform creates the Azure Bot resource and
points it at the `relay_messages_endpoint` using the password-based
`SingleTenant` app configuration. In both cases, enable the Telegram channel in
Azure Bot Service with the BotFather token after the Bot resource exists. The
token is stored in Azure Bot Service channel configuration, not in the relay
Function app settings.

The local desktop skill configuration should use the same Service Bus connection
string, queue names, Bot Framework credentials, and allowlists that Terraform
sets on the Function App.
