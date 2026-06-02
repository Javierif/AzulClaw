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

module "skill_registry" {
  source = "../../modules/skill_registry"

  resource_group_name       = var.resource_group_name
  location                  = var.location
  registry_name             = var.registry_name
  tenant_id                 = var.tenant_id
  function_app_name         = var.function_app_name
  storage_account_name      = var.storage_account_name
  service_plan_name         = var.service_plan_name
  application_insights_name = var.application_insights_name
  key_vault_name            = var.key_vault_name
  metadata_table_name       = var.metadata_table_name
  artifacts_container_name  = var.artifacts_container_name
  consumer_key              = var.consumer_key
  admin_key                 = var.admin_key
  tags                      = var.tags
}
