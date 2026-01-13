variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Default region"
  type        = string
  default     = "asia-northeast1"
}

variable "env" {
  description = "Environment name (dev/stg/prod)"
  type        = string
}
