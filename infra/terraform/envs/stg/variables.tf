variable "project_id" {
  type = string
}

variable "env" {
  type    = string
  default = "stg"
}

variable "region" {
  type    = string
  default = "asia-northeast1"
}

variable "location" {
  type    = string
  default = "ASIA-NORTHEAST1"
}

variable "raw_retention_days" {
  type    = number
  default = 60
}

variable "firestore_location_id" {
  type    = string
  default = "asia-northeast1"
}

variable "firestore_enabled" {
  type    = bool
  default = true
}

variable "secret_ids" {
  type    = list(string)
  default = ["gmail-refresh-token"]
}

variable "cloudrun_services" {
  description = "Cloud Run service images; must include web and worker."
  type        = map(string)
  validation {
    condition = contains(keys(var.cloudrun_services), "web") && contains(keys(var.cloudrun_services), "worker")
    error_message = "cloudrun_services must include both \"web\" and \"worker\" keys."
  }
}

variable "pubsub_topic_name" {
  type    = string
  default = ""
}

variable "pubsub_subscription_name" {
  type    = string
  default = ""
}

variable "gmail_watch_schedule" {
  type    = string
  default = "0 9 * * *"
}

variable "gmail_watch_path" {
  type    = string
  default = "/watch-refresh"
}

variable "gmail_watch_state_uri" {
  type    = string
  default = ""
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

variable "gmail_watch_job_name" {
  type    = string
  default = ""
}

variable "worker_service_name" {
  type    = string
  default = ""
}
