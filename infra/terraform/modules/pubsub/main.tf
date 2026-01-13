variable "project_id" {
  type = string
}

variable "topic_name" {
  type = string
}

variable "subscription_name" {
  type = string
}

variable "push_endpoint" {
  type        = string
  description = "Cloud Run worker URL"
}

variable "push_sa_email" {
  type        = string
  description = "Service account email for push authentication"
}

resource "google_pubsub_topic" "topic" {
  name    = var.topic_name
  project = var.project_id
}

resource "google_pubsub_subscription" "subscription" {
  name    = var.subscription_name
  project = var.project_id
  topic   = google_pubsub_topic.topic.name

  push_config {
    push_endpoint = var.push_endpoint
    oidc_token {
      service_account_email = var.push_sa_email
    }
  }
}

output "topic" {
  value = google_pubsub_topic.topic.name
}

output "subscription" {
  value = google_pubsub_subscription.subscription.name
}
