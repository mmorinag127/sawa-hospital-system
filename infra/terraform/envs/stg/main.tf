locals {
  env                       = var.env
  pubsub_topic_name         = var.pubsub_topic_name != "" ? var.pubsub_topic_name : "orders-${var.env}"
  pubsub_subscription_name  = var.pubsub_subscription_name != "" ? var.pubsub_subscription_name : "orders-${var.env}-push"
  pubsub_push_path          = "/pubsub/push"
  worker_service_name       = var.worker_service_name != "" ? var.worker_service_name : "worker-${var.env}"
  ocr_pipeline_service_name = var.ocr_pipeline_service_name != "" ? var.ocr_pipeline_service_name : "ocr-pipeline-${var.env}"
  ocr_trigger_topic_name    = "ocr-pipeline-${var.env}"
  ocr_trigger_sub_name      = "ocr-pipeline-${var.env}-push"
  storage_service_agent     = "service-${var.project_number}@gs-project-accounts.iam.gserviceaccount.com"
  pubsub_service_agent      = "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  worker_url                = var.cloudrun_worker_url_override != "" ? var.cloudrun_worker_url_override : "https://${local.worker_service_name}-${var.project_number}.${var.region}.run.app"
}

module "apis" {
  source     = "../../modules/apis"
  project_id = var.project_id
  apis       = var.project_services
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
    "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}",
  ]
  raw_bucket_writers = [
    "serviceAccount:${module.cloudrun.service_accounts["worker"]}",
    "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}",
  ]
  raw_bucket_admins = [
    "serviceAccount:${module.cloudrun.service_accounts["worker"]}",
    "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}",
  ]
  templates_bucket_readers = [
    "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}",
  ]
  depends_on = [module.apis, module.cloudrun]
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

module "cloudsql" {
  source              = "../../modules/cloudsql"
  project_id          = var.project_id
  region              = var.region
  instance_name       = var.db_instance_name
  db_name             = var.db_name
  db_user             = var.db_user
  db_tier             = var.db_tier
  db_password         = var.db_password
  password_secret_id  = var.db_password_secret_id
  deletion_protection = var.db_deletion_protection
  depends_on          = [module.apis, module.secrets]
}

module "cloudrun" {
  source          = "../../modules/cloudrun"
  project_id      = var.project_id
  region          = var.region
  env             = local.env
  services        = var.cloudrun_services
  request_timeout = var.cloudrun_request_timeout
  env_vars = {
    DB_NAME                     = var.db_name
    DB_USER                     = var.db_user
    DB_HOST                     = "/cloudsql/${module.cloudsql.connection_name}"
    DB_DRIVER                   = "postgresql+psycopg2"
    AUTH_DISABLED               = var.auth_disabled ? "true" : "false"
    OPERATOR_USER               = var.operator_user
    OPERATOR_PASSWORD           = var.operator_password
    GOOGLE_OAUTH_CLIENT_ID      = var.google_oauth_client_id
    ALLOWED_EMAILS              = join(",", var.allowed_emails)
    ADMIN_EMAILS                = join(",", var.admin_emails)
    GCP_PROJECT_ID              = var.project_id
    RAW_BUCKET                  = "${var.project_id}-${local.env}-raw"
    FACILITY_MASTER_PATH        = "/app/src/data/facility_master.template.json"
    CORS_ALLOW_ORIGINS          = ""
    TEMPLATE_COLLECTION         = "templates-${local.env}"
    JOB_COLLECTION              = "jobs-${local.env}"
    OCR_TEMPLATE_COLLECTION     = "templates-${local.env}"
    OCR_JOBS_COLLECTION         = "jobs-${local.env}"
    OCR_UNCLASSIFIED_COLLECTION = "unclassified-${local.env}"
    OCR_FACILITY_COLLECTION     = "facilities-${local.env}"
    OCR_INPUT_PREFIX            = "input/"
    OCR_OUTPUT_PREFIX           = "output/"
    OCR_ARTIFACTS_PREFIX        = "artifacts/"
    OCR_SAVE_ARTIFACTS          = "false"
    INGEST_JOB_STALE_MINUTES    = "30"
    API_PROXY_TARGET            = local.worker_url
  }
  service_env_vars = {
    web = {
      NEXT_PUBLIC_API_BASE_URL                 = "/api"
      API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN = "true"
      API_PROXY_TARGET_AUDIENCE                = local.worker_url
    }
    worker = {
      OCR_MAIN_PROVIDER            = "pipeline"
      OCR_PIPELINE_BUCKET          = "${var.project_id}-${local.env}-raw"
      OCR_PIPELINE_INPUT_PREFIX    = "input/"
      OCR_PIPELINE_OUTPUT_PREFIX   = "output/"
      OCR_PIPELINE_MAX_INFLIGHT    = "4"
      INGEST_MAX_WORKERS           = "6"
      GOOGLE_SERVICE_ACCOUNT_EMAIL = "worker-exec-${local.env}@${var.project_id}.iam.gserviceaccount.com"
      ADMIN_SERVICE_ACCOUNTS       = "worker-exec-${local.env}@${var.project_id}.iam.gserviceaccount.com"
    }
    "ocr-pipeline" = {
      OCR_YOMITOKU_DEVICE            = "cpu"
      OCR_YOMITOKU_DPI               = "200"
      OCR_YOMITOKU_VIS               = "true"
      OCR_YOMITOKU_VIS_PDF           = "true"
      OCR_YOMITOKU_IGNORE_LINE_BREAK = "false"
      OCR_YOMITOKU_NO_FIGURE         = "false"
      OCR_YOMITOKU_FIGURE_WIDTH      = "200"
      OCR_YOMITOKU_FIGURE_DIR        = "figures"
    }
  }
  service_resources = var.cloudrun_service_resources
  secret_env_vars   = {}
  service_secret_env_vars = {
    web    = var.cloudrun_secret_env_vars
    worker = var.cloudrun_secret_env_vars
  }
  cloudsql_instances = [module.cloudsql.connection_name]
  depends_on         = [module.apis, module.secrets, module.cloudsql]
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

module "ingest_retry_scheduler" {
  source          = "../../modules/scheduler"
  project_id      = var.project_id
  region          = var.region
  job_name        = "ingest-retry-${local.env}"
  schedule        = "0 * * * *"
  description     = "Retry pending ingest jobs"
  target_url      = "${local.worker_url}/ingest/retry?limit=10"
  target_sa_email = module.cloudrun.service_accounts["worker"]
  paused          = true
  depends_on      = [module.apis]
}

module "shipping_tracking_refresh_scheduler" {
  source          = "../../modules/scheduler"
  project_id      = var.project_id
  region          = var.region
  job_name        = "shipping-tracking-refresh-${local.env}"
  schedule        = "15 * * * *"
  description     = "Refresh pending Sagawa tracking statuses"
  target_url      = "${local.worker_url}/shipping/status/refresh-pending?limit=100&max_age_days=14"
  target_sa_email = module.cloudrun.service_accounts["worker"]
  paused          = true
  depends_on      = [module.apis]
}

module "iam" {
  source     = "../../modules/iam"
  project_id = var.project_id
  run_invoker_bindings = [
    {
      member   = "serviceAccount:${module.cloudrun.service_accounts["web"]}"
      service  = local.worker_service_name
      location = var.region
    },
    {
      member   = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      service  = local.worker_service_name
      location = var.region
    },
    {
      member   = "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}"
      service  = local.ocr_pipeline_service_name
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
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      role   = "roles/datastore.user"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}"
      role   = "roles/eventarc.eventReceiver"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}"
      role   = "roles/datastore.user"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}"
      role   = "roles/secretmanager.secretAccessor"
    }
  ]
  depends_on = [module.apis, module.cloudrun]
}

resource "google_service_account_iam_member" "worker_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.cloudrun.service_accounts["worker"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
}

resource "google_service_account_iam_member" "cloudscheduler_worker_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.cloudrun.service_accounts["worker"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${var.project_number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "pubsub_service_agent_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:${local.pubsub_service_agent}"
}

resource "google_service_account_iam_member" "pubsub_ocr_pipeline_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.cloudrun.service_accounts["ocr-pipeline"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${local.pubsub_service_agent}"
}

resource "google_pubsub_topic" "ocr_pipeline_trigger" {
  name    = local.ocr_trigger_topic_name
  project = var.project_id
}

resource "google_pubsub_topic_iam_member" "ocr_pipeline_storage_publisher" {
  topic   = google_pubsub_topic.ocr_pipeline_trigger.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${local.storage_service_agent}"
  project = var.project_id
}

resource "google_pubsub_subscription" "ocr_pipeline_trigger_push" {
  name    = local.ocr_trigger_sub_name
  project = var.project_id
  topic   = google_pubsub_topic.ocr_pipeline_trigger.name

  push_config {
    push_endpoint = module.cloudrun.service_urls["ocr-pipeline"]
    oidc_token {
      service_account_email = module.cloudrun.service_accounts["ocr-pipeline"]
    }
  }
}

resource "google_storage_notification" "ocr_pipeline_input" {
  bucket         = module.storage.raw_bucket
  topic          = google_pubsub_topic.ocr_pipeline_trigger.id
  payload_format = "JSON_API_V1"
  event_types    = ["OBJECT_FINALIZE"]

  depends_on = [
    module.apis,
    module.cloudrun,
    module.storage,
    module.iam,
    google_project_iam_member.pubsub_service_agent_token_creator,
    google_service_account_iam_member.pubsub_ocr_pipeline_token_creator,
    google_pubsub_topic_iam_member.ocr_pipeline_storage_publisher,
  ]
}

module "monitoring" {
  source                   = "../../modules/monitoring"
  project_id               = var.project_id
  env                      = local.env
  region                   = var.region
  worker_service_name      = local.worker_service_name
  pubsub_subscription_name = local.pubsub_subscription_name
  notification_emails      = var.notification_emails
  notification_channels    = var.notification_channels
  depends_on               = [module.apis]
}
