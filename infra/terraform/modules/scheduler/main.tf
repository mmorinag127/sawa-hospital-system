variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast1"
}

variable "job_name" {
  type = string
}

variable "schedule" {
  type        = string
  description = "Cron schedule, e.g., every 24h"
  default     = "0 9 * * *"
}

variable "target_url" {
  type = string
}

variable "target_sa_email" {
  type = string
}

variable "paused" {
  type    = bool
  default = false
}

variable "description" {
  type    = string
  default = "Scheduled job"
}

resource "google_cloud_scheduler_job" "job" {
  name        = var.job_name
  project     = var.project_id
  region      = var.region
  description = var.description
  schedule    = var.schedule
  time_zone   = "Asia/Tokyo"
  paused      = var.paused

  http_target {
    http_method = "POST"
    uri         = var.target_url
    oidc_token {
      service_account_email = var.target_sa_email
    }
  }
}

output "job_name" {
  value = google_cloud_scheduler_job.job.name
}
