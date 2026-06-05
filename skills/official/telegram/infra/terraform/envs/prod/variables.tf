variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "function_app_name" { type = string }
variable "storage_account_name" { type = string }
variable "service_plan_name" { type = string }
variable "application_insights_name" { type = string }
variable "service_bus_namespace_name" { type = string }

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

variable "service_bus_use_sessions" {
  type    = string
  default = "auto"
}

variable "service_bus_outbound_requires_session" {
  type    = bool
  default = true
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

variable "tags" {
  type    = map(string)
  default = {}
}
