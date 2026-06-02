# Telegram Terraform

This folder owns Azure infrastructure needed only by the Telegram skill.

The first implementation should create or reference:

- Azure Function App for the relay
- Storage account for Functions
- Service Bus queues for inbound and outbound relay traffic
- Application Insights
- app settings for Bot Framework and Telegram allowlists
