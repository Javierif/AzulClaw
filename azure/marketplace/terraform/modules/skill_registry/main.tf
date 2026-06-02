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

resource "azurerm_storage_container" "artifacts" {
  name                  = var.artifacts_container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_table" "metadata" {
  name                 = var.metadata_table_name
  storage_account_name = azurerm_storage_account.this.name
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
  workspace_id        = null
  tags                = var.tags
}

resource "azurerm_key_vault" "this" {
  name                       = var.key_vault_name
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = var.tags
}

resource "azurerm_linux_function_app" "this" {
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
      "FUNCTIONS_WORKER_RUNTIME"                 = "python"
      "SCM_DO_BUILD_DURING_DEPLOYMENT"          = "true"
      "WEBSITE_RUN_FROM_PACKAGE"                = var.run_from_package
      "AZUL_SKILL_REGISTRY_NAME"                = var.registry_name
      "AZUL_SKILL_REGISTRY_STORAGE_MODE"        = "azure"
      "AZUL_SKILL_REGISTRY_AZURE_CONNECTION_STRING" = azurerm_storage_account.this.primary_connection_string
      "AZUL_SKILL_REGISTRY_AZURE_TABLE_NAME"    = var.metadata_table_name
      "AZUL_SKILL_REGISTRY_AZURE_BLOB_CONTAINER" = var.artifacts_container_name
      "AZUL_SKILL_REGISTRY_CONSUMER_KEY"        = var.consumer_key
      "AZUL_SKILL_REGISTRY_ADMIN_KEY"           = var.admin_key
      "APPLICATIONINSIGHTS_CONNECTION_STRING"   = azurerm_application_insights.this.connection_string
      "AzureWebJobsStorage"                     = azurerm_storage_account.this.primary_connection_string
    },
    var.extra_app_settings,
  )
}
