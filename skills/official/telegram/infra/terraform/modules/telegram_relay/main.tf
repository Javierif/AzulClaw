terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "this" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = var.storage_replication_type
  min_tls_version          = "TLS1_2"
  tags                     = var.tags
}

resource "azurerm_service_plan" "this" {
  name                = var.service_plan_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  os_type             = "Linux"
  sku_name            = var.service_plan_sku
  tags                = var.tags
}

resource "azurerm_application_insights" "this" {
  name                = var.application_insights_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  application_type    = "web"
  tags                = var.tags
}

resource "azurerm_servicebus_namespace" "this" {
  name                = var.service_bus_namespace_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = var.service_bus_sku
  tags                = var.tags
}

resource "azurerm_servicebus_namespace_authorization_rule" "relay" {
  name         = "telegram-relay"
  namespace_id = azurerm_servicebus_namespace.this.id

  listen = true
  send   = true
  manage = false
}

resource "azurerm_servicebus_queue" "inbound" {
  name         = var.service_bus_inbound_queue
  namespace_id = azurerm_servicebus_namespace.this.id

  max_delivery_count = var.service_bus_max_delivery_count
  requires_session   = false
}

resource "azurerm_servicebus_queue" "outbound" {
  name         = var.service_bus_outbound_queue
  namespace_id = azurerm_servicebus_namespace.this.id

  max_delivery_count = var.service_bus_max_delivery_count
  requires_session   = var.service_bus_outbound_requires_session
}

resource "azurerm_linux_function_app" "relay" {
  name                       = var.function_app_name
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  service_plan_id            = azurerm_service_plan.this.id
  storage_account_name       = azurerm_storage_account.this.name
  storage_account_access_key = azurerm_storage_account.this.primary_access_key
  https_only                 = true
  tags                       = var.tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    application_stack {
      python_version = var.python_version
    }
  }

  app_settings = merge(
    {
      "FUNCTIONS_WORKER_RUNTIME"              = "python"
      "SCM_DO_BUILD_DURING_DEPLOYMENT"        = "true"
      "WEBSITE_RUN_FROM_PACKAGE"              = var.run_from_package
      "AzureWebJobsStorage"                   = azurerm_storage_account.this.primary_connection_string
      "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.this.connection_string
      "SERVICE_BUS_CONNECTION_STRING"         = azurerm_servicebus_namespace_authorization_rule.relay.primary_connection_string
      "SERVICE_BUS_INBOUND_QUEUE"             = azurerm_servicebus_queue.inbound.name
      "SERVICE_BUS_OUTBOUND_QUEUE"            = azurerm_servicebus_queue.outbound.name
      "SERVICE_BUS_USE_SESSIONS"              = var.service_bus_use_sessions
      "BOT_SYNC_REPLY_TIMEOUT_SECONDS"        = tostring(var.bot_sync_reply_timeout_seconds)
      "BOT_RELAY_REQUIRE_AUTH"                = tostring(var.bot_relay_require_auth)
      "MicrosoftAppId"                        = var.microsoft_app_id
      "MicrosoftAppPassword"                  = var.microsoft_app_password
      "MicrosoftAppTenantId"                  = var.microsoft_app_tenant_id
      "TELEGRAM_ALLOWED_USER_IDS"             = var.telegram_allowed_user_ids
      "TELEGRAM_ALLOWED_CHAT_IDS"             = var.telegram_allowed_chat_ids
    },
    var.extra_app_settings,
  )
}

resource "azurerm_bot_service_azure_bot" "this" {
  count = var.bot_service_name != "" ? 1 : 0

  name                    = var.bot_service_name
  resource_group_name     = azurerm_resource_group.this.name
  location                = "global"
  sku                     = var.bot_service_sku
  microsoft_app_id        = var.microsoft_app_id
  microsoft_app_type      = var.microsoft_app_type
  microsoft_app_tenant_id = var.microsoft_app_tenant_id
  endpoint                = "https://${azurerm_linux_function_app.relay.default_hostname}/api/messages"
  tags                    = var.tags
}
