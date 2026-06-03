terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

module "telegram_relay" {
  source = "../../modules/telegram_relay"

  resource_group_name                   = var.resource_group_name
  location                              = var.location
  function_app_name                     = var.function_app_name
  storage_account_name                  = var.storage_account_name
  service_plan_name                     = var.service_plan_name
  application_insights_name             = var.application_insights_name
  service_bus_namespace_name            = var.service_bus_namespace_name
  bot_service_name                      = var.bot_service_name
  bot_service_sku                       = var.bot_service_sku
  microsoft_app_type                    = var.microsoft_app_type
  service_bus_inbound_queue             = var.service_bus_inbound_queue
  service_bus_outbound_queue            = var.service_bus_outbound_queue
  service_bus_use_sessions              = var.service_bus_use_sessions
  bot_sync_reply_timeout_seconds        = var.bot_sync_reply_timeout_seconds
  bot_relay_require_auth                = var.bot_relay_require_auth
  microsoft_app_id                      = var.microsoft_app_id
  microsoft_app_password                = var.microsoft_app_password
  microsoft_app_tenant_id               = var.microsoft_app_tenant_id
  telegram_allowed_user_ids             = var.telegram_allowed_user_ids
  telegram_allowed_chat_ids             = var.telegram_allowed_chat_ids
  service_bus_outbound_requires_session = var.service_bus_outbound_requires_session
  tags                                  = var.tags
}

output "relay_messages_endpoint" {
  value = module.telegram_relay.relay_messages_endpoint
}

output "relay_health_endpoint" {
  value = module.telegram_relay.relay_health_endpoint
}

output "service_bus_connection_string" {
  value     = module.telegram_relay.service_bus_connection_string
  sensitive = true
}

output "bot_service_name" {
  value = module.telegram_relay.bot_service_name
}
