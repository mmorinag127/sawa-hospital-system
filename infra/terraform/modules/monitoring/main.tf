variable "project_id" {
  type = string
}

variable "env" {
  type = string
}

variable "region" {
  type = string
}

variable "worker_service_name" {
  type = string
}

variable "pubsub_subscription_name" {
  type = string
}

variable "notification_channels" {
  type    = list(string)
  default = []
}

variable "notification_emails" {
  type    = list(string)
  default = []
}

resource "google_monitoring_notification_channel" "email" {
  for_each = toset(var.notification_emails)
  display_name = "email-${replace(each.key, "@", "-")}"
  type         = "email"
  labels = {
    email_address = each.key
  }
}

locals {
  notification_channel_ids = concat(
    var.notification_channels,
    [for channel in google_monitoring_notification_channel.email : channel.id],
  )
}

resource "google_logging_metric" "watch_refresh_failures" {
  name   = "watch_refresh_failures_${var.env}"
  filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${var.worker_service_name}"
    ("Watch refresh skipped" OR "watch refresh failed")
  EOT
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "watch_refresh_failure" {
  display_name = "watch-refresh-failure-${var.env}"
  combiner     = "OR"
  notification_channels = local.notification_channel_ids

  conditions {
    display_name = "Watch refresh failures detected"
    condition_threshold {
      filter = "resource.type=\"cloud_run_revision\" resource.label.service_name=\"${var.worker_service_name}\" metric.type=\"logging.googleapis.com/user/${google_logging_metric.watch_refresh_failures.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_DELTA"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "cloud_run_5xx" {
  display_name = "worker-5xx-errors-${var.env}"
  combiner     = "OR"
  notification_channels = local.notification_channel_ids

  conditions {
    display_name = "Worker 5xx responses"
    condition_threshold {
      filter = "resource.type=\"cloud_run_revision\" resource.label.service_name=\"${var.worker_service_name}\" metric.type=\"run.googleapis.com/request_count\" metric.label.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_DELTA"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "pubsub_backlog" {
  display_name = "pubsub-backlog-${var.env}"
  combiner     = "OR"
  notification_channels = local.notification_channel_ids

  conditions {
    display_name = "Pub/Sub backlog"
    condition_threshold {
      filter = "resource.type=\"pubsub_subscription\" resource.label.subscription_id=\"${var.pubsub_subscription_name}\" metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
}

output "monitoring_configured" {
  value = true
}
