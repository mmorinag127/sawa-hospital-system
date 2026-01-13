variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "instance_name" {
  type = string
}

variable "db_name" {
  type = string
}

variable "db_user" {
  type = string
}

variable "db_tier" {
  type    = string
  default = "db-f1-micro"
}

variable "db_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "password_secret_id" {
  type    = string
  default = ""
}

variable "deletion_protection" {
  type    = bool
  default = false
}

resource "random_password" "db" {
  length  = 24
  special = false
}

locals {
  password = var.db_password != "" ? var.db_password : random_password.db.result
}

resource "google_sql_database_instance" "main" {
  name             = var.instance_name
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_15"

  settings {
    tier = var.db_tier
  }

  deletion_protection = var.deletion_protection
}

resource "google_sql_database" "default" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
  project  = var.project_id
}

resource "google_sql_user" "default" {
  name     = var.db_user
  instance = google_sql_database_instance.main.name
  project  = var.project_id
  password = local.password
}

resource "google_secret_manager_secret_version" "db_password" {
  count       = var.password_secret_id != "" ? 1 : 0
  secret      = "projects/${var.project_id}/secrets/${var.password_secret_id}"
  secret_data = local.password
}

output "connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "db_name" {
  value = google_sql_database.default.name
}

output "db_user" {
  value = google_sql_user.default.name
}
