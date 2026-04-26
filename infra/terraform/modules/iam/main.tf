variable "project_id" {
  type = string
}

variable "run_invoker_bindings" {
  description = "List of {member, service, location} to grant run.invoker"
  type = list(object({
    member   = string
    service  = string
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

locals {
  run_invoker_binding_map = {
    for b in var.run_invoker_bindings :
    "${b.location}|${b.service}|${b.member}" => b
  }

  project_role_binding_map = {
    for b in var.project_role_bindings :
    "${b.role}|${b.member}" => b
  }
}

resource "google_cloud_run_service_iam_member" "invoker" {
  for_each = local.run_invoker_binding_map
  location = each.value.location
  project  = var.project_id
  service  = each.value.service
  role     = "roles/run.invoker"
  member   = each.value.member
}

resource "google_project_iam_member" "project_roles" {
  for_each = local.project_role_binding_map
  project  = var.project_id
  role     = each.value.role
  member   = each.value.member
}
