output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "function_app_name" {
  value = azurerm_linux_function_app.relay.name
}

output "function_app_default_hostname" {
  value = azurerm_linux_function_app.relay.default_hostname
}

output "relay_messages_endpoint" {
  value = "https://${azurerm_linux_function_app.relay.default_hostname}/api/messages"
}

output "relay_health_endpoint" {
  value = "https://${azurerm_linux_function_app.relay.default_hostname}/api/health"
}

output "service_bus_namespace_name" {
  value = azurerm_servicebus_namespace.this.name
}

output "service_bus_inbound_queue" {
  value = azurerm_servicebus_queue.inbound.name
}

output "service_bus_outbound_queue" {
  value = azurerm_servicebus_queue.outbound.name
}

output "service_bus_connection_string" {
  value     = azurerm_servicebus_namespace_authorization_rule.relay.primary_connection_string
  sensitive = true
}

output "bot_service_name" {
  value = try(azurerm_bot_service_azure_bot.this[0].name, "")
}

output "bot_service_id" {
  value = try(azurerm_bot_service_azure_bot.this[0].id, "")
}
