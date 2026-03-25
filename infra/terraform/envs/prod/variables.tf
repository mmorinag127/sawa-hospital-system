variable "project_id" {
  type = string
}

variable "project_number" {
  description = "Numeric GCP project number used for service-agent IAM bindings."
  type        = string
  default     = "167795504375"
}

variable "env" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "asia-northeast2"
}

variable "location" {
  type    = string
  default = "ASIA-NORTHEAST2"
}

variable "raw_retention_days" {
  type    = number
  default = 60
}

variable "firestore_location_id" {
  type    = string
  default = "asia-northeast2"
}

variable "firestore_enabled" {
  type    = bool
  default = true
}

variable "secret_ids" {
  type = list(string)
  default = [
    "db-password",
  ]
}

variable "cloudrun_services" {
  description = "Cloud Run service images; must include web, worker, and ocr-pipeline."
  type        = map(string)
  validation {
    condition     = contains(keys(var.cloudrun_services), "web") && contains(keys(var.cloudrun_services), "worker") && contains(keys(var.cloudrun_services), "ocr-pipeline")
    error_message = "cloudrun_services must include \"web\", \"worker\", and \"ocr-pipeline\" keys."
  }
}

variable "cloudrun_secret_env_vars" {
  description = "Map of env var name to Secret Manager secret id."
  type        = map(string)
  default     = {}
}

variable "cloudrun_request_timeout" {
  description = "Request timeout for Cloud Run services (e.g. 900s)."
  type        = string
  default     = "900s"
}

variable "cloudrun_service_resources" {
  description = "Cloud Run per-service resource limits."
  type        = map(map(string))
  default     = {}
}

variable "pubsub_topic_name" {
  type    = string
  default = ""
}

variable "pubsub_subscription_name" {
  type    = string
  default = ""
}

variable "auth_disabled" {
  type    = bool
  default = false
}

variable "operator_user" {
  type    = string
  default = ""
}

variable "operator_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "google_oauth_client_id" {
  type    = string
  default = ""
}

variable "allowed_emails" {
  type    = list(string)
  default = []
}

variable "admin_emails" {
  type    = list(string)
  default = []
}

variable "db_instance_name" {
  type    = string
  default = "orders-prod"
}

variable "db_name" {
  type    = string
  default = "orders"
}

variable "db_user" {
  type    = string
  default = "orders_app"
}

variable "db_tier" {
  type    = string
  default = "db-g1-small"
}

variable "db_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "db_password_secret_id" {
  type    = string
  default = "db-password"
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "notification_emails" {
  type    = list(string)
  default = []
}

variable "notification_channels" {
  type    = list(string)
  default = []
}

variable "cloudrun_worker_url_override" {
  type    = string
  default = ""
}

variable "worker_service_name" {
  type    = string
  default = ""
}

variable "ocr_pipeline_service_name" {
  type    = string
  default = ""
}
