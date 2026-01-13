variable "project_id" {
  type = string
}

variable "run_invoker_bindings" {
  description = "List of {member, service, location} to grant run.invoker"
  type = list(object({
    member  = string
    service = string
    location = string
  }))
  default = []
}

variable "project_role_bindings" {
  description = "List of {member, role} to grant project-level roles."
  type = list(object({
    member = string
    role   = string
  }))
  default = []
}

resource "google_cloud_run_service_iam_member" "invoker" {
  for_each = { for idx, b in var.run_invoker_bindings : idx => b }
  location = each.value.location
  project  = var.project_id
  service  = each.value.service
  role     = "roles/run.invoker"
  member   = each.value.member
}

resource "google_project_iam_member" "project_roles" {
  for_each = { for idx, b in var.project_role_bindings : idx => b }
  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}
