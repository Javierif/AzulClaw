variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "function_app_name" {
  type = string
}

variable "storage_account_name" {
  type = string
}

variable "service_plan_name" {
  type = string
}

variable "application_insights_name" {
  type = string
}

variable "service_bus_namespace_name" {
  type = string
}

variable "bot_service_name" {
  type    = string
  default = ""
}

variable "bot_service_sku" {
  type    = string
  default = "F0"
}

variable "microsoft_app_type" {
  type    = string
  default = "SingleTenant"

  validation {
    condition     = var.microsoft_app_type == "SingleTenant"
    error_message = "microsoft_app_type must be SingleTenant for this password-based Telegram relay module."
  }
}

variable "service_bus_inbound_queue" {
  type    = string
  default = "bot-inbound"
}

variable "service_bus_outbound_queue" {
  type    = string
  default = "bot-outbound"
}

variable "service_bus_sku" {
  type    = string
  default = "Standard"
}

variable "service_bus_max_delivery_count" {
  type    = number
  default = 10
}

variable "service_bus_outbound_requires_session" {
  type    = bool
  default = true
}

variable "service_bus_use_sessions" {
  type    = string
  default = "auto"

  validation {
    condition     = contains(["auto", "true", "false"], var.service_bus_use_sessions)
    error_message = "service_bus_use_sessions must be auto, true, or false."
  }
}

variable "bot_sync_reply_timeout_seconds" {
  type    = number
  default = 6.8
}

variable "bot_relay_require_auth" {
  type    = bool
  default = true
}

variable "microsoft_app_id" {
  type = string
}

variable "microsoft_app_password" {
  type      = string
  sensitive = true
}

variable "microsoft_app_tenant_id" {
  type    = string
  default = ""
}

variable "telegram_allowed_user_ids" {
  type    = string
  default = ""
}

variable "telegram_allowed_chat_ids" {
  type    = string
  default = ""
}

variable "storage_replication_type" {
  type    = string
  default = "LRS"
}

variable "service_plan_sku" {
  type    = string
  default = "Y1"
}

variable "python_version" {
  type    = string
  default = "3.11"
}

variable "run_from_package" {
  type    = string
  default = "1"
}

variable "extra_app_settings" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
