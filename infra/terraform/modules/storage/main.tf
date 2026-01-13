variable "project_id" {
  type = string
}

variable "location" {
  type    = string
  default = "ASIA-NORTHEAST1"
}

variable "env" {
  type = string
}

variable "raw_retention_days" {
  type    = number
  default = 60
}

variable "raw_bucket_readers" {
  type    = list(string)
  default = []
}

variable "raw_bucket_writers" {
  type    = list(string)
  default = []
}

variable "raw_bucket_admins" {
  type    = list(string)
  default = []
}

variable "templates_bucket_readers" {
  type    = list(string)
  default = []
}

locals {
  name_prefix = "${var.project_id}-${var.env}"
}

resource "google_storage_bucket" "raw" {
  name                        = "${local.name_prefix}-raw"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = var.raw_retention_days }
  }
}

resource "google_storage_bucket_iam_member" "raw_object_viewer" {
  for_each = { for idx, member in var.raw_bucket_readers : idx => member }
  bucket   = google_storage_bucket.raw.name
  role     = "roles/storage.objectViewer"
  member   = each.value
}

resource "google_storage_bucket_iam_member" "raw_object_creator" {
  for_each = { for idx, member in var.raw_bucket_writers : idx => member }
  bucket   = google_storage_bucket.raw.name
  role     = "roles/storage.objectCreator"
  member   = each.value
}

resource "google_storage_bucket_iam_member" "raw_object_admin" {
  for_each = { for idx, member in var.raw_bucket_admins : idx => member }
  bucket   = google_storage_bucket.raw.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}

resource "google_storage_bucket" "templates" {
  name                        = "${local.name_prefix}-templates"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
}

resource "google_storage_bucket_iam_member" "templates_object_viewer" {
  for_each = { for idx, member in var.templates_bucket_readers : idx => member }
  bucket   = google_storage_bucket.templates.name
  role     = "roles/storage.objectViewer"
  member   = each.value
}

resource "google_storage_bucket" "exports" {
  name                        = "${local.name_prefix}-exports"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "templates_bucket" {
  value = google_storage_bucket.templates.name
}

output "exports_bucket" {
  value = google_storage_bucket.exports.name
}
