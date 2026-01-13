variable "project_id" {
  type = string
}

variable "apis" {
  type        = list(string)
  description = "List of APIs to enable"
  default = [
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "sqladmin.googleapis.com",
    "eventarc.googleapis.com"
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(var.apis)
  project  = var.project_id
  service  = each.key
  disable_dependent_services = false
  disable_on_destroy         = false
}

output "enabled_apis" {
  value = [for svc in values(google_project_service.enabled) : svc.service]
}
