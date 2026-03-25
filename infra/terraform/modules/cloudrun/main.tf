variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "env" {
  type = string
}

variable "services" {
  description = "Map of Cloud Run services {name => image}"
  type        = map(string)
}

variable "env_vars" {
  description = "Environment variables applied to all Cloud Run services."
  type        = map(string)
  default     = {}
}

variable "service_env_vars" {
  description = "Map of service name to environment variable map."
  type        = map(map(string))
  default     = {}
}

variable "service_resources" {
  description = "Map of service name to resource limits (cpu/memory)."
  type        = map(map(string))
  default     = {}
}

variable "secret_env_vars" {
  description = "Map of env var name to Secret Manager secret id (latest version)."
  type        = map(string)
  default     = {}
}

variable "service_secret_env_vars" {
  description = "Map of service name to secret env var map."
  type        = map(map(string))
  default     = {}
}

variable "request_timeout" {
  description = "Request timeout for Cloud Run services (e.g. 900s)."
  type        = string
  default     = "300s"
}

variable "cloudsql_instances" {
  description = "List of Cloud SQL instance connection names for Cloud Run."
  type        = list(string)
  default     = []
}

resource "google_service_account" "run_exec" {
  for_each = var.services
  account_id   = "${each.key}-exec-${var.env}"
  display_name = "Cloud Run exec ${each.key} (${var.env})"
}

locals {
  service_secret_refs = {
    for service_name, _ in var.services :
    service_name => merge(var.secret_env_vars, lookup(var.service_secret_env_vars, service_name, {}))
  }
}

resource "google_project_iam_member" "secret_accessor" {
  for_each = {
    for service_name, secret_map in local.service_secret_refs :
    service_name => service_name
    if length(secret_map) > 0
  }
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.run_exec[each.key].email}"
}

resource "google_project_iam_member" "cloudsql_client" {
  for_each = length(var.cloudsql_instances) > 0 ? var.services : {}
  project  = var.project_id
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${google_service_account.run_exec[each.key].email}"
}

resource "google_cloud_run_v2_service" "service" {
  for_each = var.services
  name     = "${each.key}-${var.env}"
  location = var.region
  project  = var.project_id
  depends_on = [
    google_project_iam_member.secret_accessor,
    google_project_iam_member.cloudsql_client,
  ]

  template {
    timeout         = var.request_timeout
    service_account = google_service_account.run_exec[each.key].email
    containers {
      image = each.value
      dynamic "resources" {
        for_each = length(lookup(var.service_resources, each.key, {})) > 0 ? [lookup(var.service_resources, each.key, {})] : []
        content {
          limits = resources.value
        }
      }
      dynamic "volume_mounts" {
        for_each = length(var.cloudsql_instances) > 0 ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
      dynamic "env" {
        for_each = merge(var.env_vars, lookup(var.service_env_vars, each.key, {}))
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = merge(var.secret_env_vars, lookup(var.service_secret_env_vars, each.key, {}))
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
    dynamic "volumes" {
      for_each = length(var.cloudsql_instances) > 0 ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = var.cloudsql_instances
        }
      }
    }
  }
}

output "service_urls" {
  value = { for k, v in google_cloud_run_v2_service.service : k => v.uri }
}

output "service_accounts" {
  value = { for k, v in google_service_account.run_exec : k => v.email }
}
