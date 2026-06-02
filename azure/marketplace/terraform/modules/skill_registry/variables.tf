variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "registry_name" {
  type = string
}

variable "tenant_id" {
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

variable "key_vault_name" {
  type = string
}

variable "metadata_table_name" {
  type    = string
  default = "skillregistry"
}

variable "artifacts_container_name" {
  type    = string
  default = "skill-artifacts"
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

variable "consumer_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "admin_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "extra_app_settings" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
