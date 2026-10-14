locals {
  facility_master_uri_parts = regex("^gs://([^/]+)/(.+)$", trimspace(var.facility_master_uri))
}

resource "google_storage_bucket_iam_member" "facility_master_writer" {
  bucket = local.facility_master_uri_parts[0]
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"

  # Exact object equality grants neither adjacent-object writes nor bucket listing.
  condition {
    title      = "canonical-facility-master-object"
    expression = "resource.name == ${jsonencode("projects/_/buckets/${local.facility_master_uri_parts[0]}/objects/${local.facility_master_uri_parts[1]}")}"
  }

  depends_on = [module.storage]
}
