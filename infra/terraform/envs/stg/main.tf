locals {
  env = var.env
  pubsub_topic_name = var.pubsub_topic_name != "" ? var.pubsub_topic_name : "orders-${var.env}"
  pubsub_subscription_name = var.pubsub_subscription_name != "" ? var.pubsub_subscription_name : "orders-${var.env}-push"
  pubsub_push_path = "/pubsub/push"
  worker_service_name = var.worker_service_name != "" ? var.worker_service_name : "worker-${var.env}"
  worker_url = coalesce(module.cloudrun.service_urls["worker"], var.cloudrun_worker_url_override)
}

module "apis" {
  source     = "../../modules/apis"
  project_id = var.project_id
}

module "storage" {
  source             = "../../modules/storage"
  project_id         = var.project_id
  location           = var.location
  env                = local.env
  raw_retention_days = var.raw_retention_days
  raw_bucket_readers = [
    "serviceAccount:${module.cloudrun.service_accounts["web"]}",
    "serviceAccount:${module.cloudrun.service_accounts["worker"]}",
  ]
  depends_on         = [module.apis, module.cloudrun]
}

module "firestore" {
  source      = "../../modules/firestore"
  project_id  = var.project_id
  location_id = var.firestore_location_id
  enabled     = var.firestore_enabled
  depends_on  = [module.apis]
}

module "secrets" {
  source     = "../../modules/secrets"
  project_id = var.project_id
  secret_ids = var.secret_ids
  depends_on = [module.apis]
}

module "cloudrun" {
  source    = "../../modules/cloudrun"
  project_id = var.project_id
  region     = var.region
  env        = local.env
  services   = var.cloudrun_services
  env_vars = {
    GCP_PROJECT_ID          = var.project_id
  }
  depends_on = [module.apis]
}

module "pubsub" {
  source            = "../../modules/pubsub"
  project_id        = var.project_id
  topic_name        = local.pubsub_topic_name
  subscription_name = local.pubsub_subscription_name
  push_endpoint     = "${local.worker_url}${local.pubsub_push_path}"
  push_sa_email     = module.cloudrun.service_accounts["worker"]
  depends_on        = [module.apis]
}

module "iam" {
  source     = "../../modules/iam"
  project_id = var.project_id
  run_invoker_bindings = [
    {
      member  = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      service = local.worker_service_name
      location = var.region
    }
  ]
  project_role_bindings = [
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["web"]}"
      role   = "roles/storage.objectViewer"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      role   = "roles/storage.objectViewer"
    }
  ]
  depends_on = [module.apis, module.cloudrun]
}

module "monitoring" {
  source     = "../../modules/monitoring"
  project_id = var.project_id
  env        = local.env
  region     = var.region
  worker_service_name      = local.worker_service_name
  pubsub_subscription_name = local.pubsub_subscription_name
  notification_emails      = var.notification_emails
  notification_channels    = var.notification_channels
  depends_on = [module.apis]
}
