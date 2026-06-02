output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "function_app_name" {
  value = azurerm_linux_function_app.this.name
}

output "function_app_default_hostname" {
  value = azurerm_linux_function_app.this.default_hostname
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}

output "artifacts_container_name" {
  value = azurerm_storage_container.artifacts.name
}

output "metadata_table_name" {
  value = azurerm_storage_table.metadata.name
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}
