variable "project_id" {
  type = string
}

variable "location_id" {
  type    = string
  default = "asia-northeast1"
}

variable "enabled" {
  type    = bool
  default = true
}

resource "google_firestore_database" "default" {
  count       = var.enabled ? 1 : 0
  project     = var.project_id
  name        = "(default)"
  location_id = var.location_id
  type        = "FIRESTORE_NATIVE"
}

output "firestore_database" {
  value = var.enabled ? google_firestore_database.default[0].name : null
}
