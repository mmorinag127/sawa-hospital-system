locals {
  env = var.env
  pubsub_topic_name = var.pubsub_topic_name != "" ? var.pubsub_topic_name : "orders-${var.env}"
  pubsub_subscription_name = var.pubsub_subscription_name != "" ? var.pubsub_subscription_name : "orders-${var.env}-push"
  pubsub_push_path = "/pubsub/push"
  watch_path = var.gmail_watch_path != "" ? var.gmail_watch_path : "/watch-refresh"
  watch_job_name = var.gmail_watch_job_name != "" ? var.gmail_watch_job_name : "gmail-watch-refresh-${var.env}"
  worker_service_name = var.worker_service_name != "" ? var.worker_service_name : "worker-${var.env}"
  ocr_pipeline_service_name = var.ocr_pipeline_service_name != "" ? var.ocr_pipeline_service_name : "ocr-pipeline-${var.env}"
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

module "cloudsql" {
  source                = "../../modules/cloudsql"
  project_id            = var.project_id
  region                = var.region
  instance_name         = var.db_instance_name
  db_name               = var.db_name
  db_user               = var.db_user
  db_tier               = var.db_tier
  db_password           = var.db_password
  password_secret_id    = var.db_password_secret_id
  deletion_protection   = var.db_deletion_protection
  depends_on            = [module.apis, module.secrets]
}

  module "cloudrun" {
  source    = "../../modules/cloudrun"
  project_id = var.project_id
  region     = var.region
  env        = local.env
  services   = var.cloudrun_services
  request_timeout = var.cloudrun_request_timeout
  env_vars = {
    DB_NAME                  = var.db_name
    DB_USER                  = var.db_user
    DB_HOST                  = "/cloudsql/${module.cloudsql.connection_name}"
    DB_DRIVER                = "postgresql+psycopg2"
    AUTH_DISABLED            = var.auth_disabled ? "true" : "false"
    OPERATOR_USER            = var.operator_user
    OPERATOR_PASSWORD        = var.operator_password
    GOOGLE_OAUTH_CLIENT_ID   = var.google_oauth_client_id
    ALLOWED_EMAILS           = join(",", var.allowed_emails)
    ADMIN_EMAILS             = join(",", var.admin_emails)
    GCP_PROJECT_ID          = var.project_id
    GMAIL_WATCH_TOPIC        = "projects/${var.project_id}/topics/${local.pubsub_topic_name}"
    RAW_BUCKET               = "${var.project_id}-${local.env}-raw"
    GMAIL_INGEST_QUERY       = var.gmail_ingest_query
    GMAIL_INGEST_LABEL_IDS   = join(",", var.gmail_ingest_label_ids)
    GMAIL_INGEST_MAX_RESULTS = tostring(var.gmail_ingest_max_results)
    GMAIL_INGEST_MARK_READ   = var.gmail_ingest_mark_read ? "true" : "false"
    GMAIL_INGEST_PREFIX      = var.gmail_ingest_prefix
    GMAIL_WATCH_STATE_URI    = var.gmail_watch_state_uri != "" ? var.gmail_watch_state_uri : "gs://${var.project_id}-${local.env}-raw/gmail/watch_state.json"
    FACILITY_MASTER_PATH     = "/app/src/data/facility_master.test.json"
    CORS_ALLOW_ORIGINS       = "https://web-dev-m3j47i2tgq-an.a.run.app,https://web-dev-161384524548.asia-northeast1.run.app"
    TEMPLATE_COLLECTION      = "templates"
    JOB_COLLECTION           = "jobs"
    OCR_INPUT_PREFIX         = "input/"
    OCR_OUTPUT_PREFIX        = "output/"
    OCR_ARTIFACTS_PREFIX     = "artifacts/"
    OCR_SAVE_ARTIFACTS       = "false"
    INGEST_JOB_STALE_MINUTES = "30"
  }
  service_env_vars = {
    worker = {
      OCR_MAIN_PROVIDER          = "pipeline"
      OCR_PIPELINE_BUCKET        = "${var.project_id}-${local.env}-raw"
      OCR_PIPELINE_INPUT_PREFIX  = "input/"
      OCR_PIPELINE_OUTPUT_PREFIX = "output/"
    }
    "ocr-pipeline" = {
      OCR_YOMITOKU_DEVICE          = "cpu"
      OCR_YOMITOKU_DPI             = "200"
      OCR_YOMITOKU_VIS             = "true"
      OCR_YOMITOKU_VIS_PDF         = "true"
      OCR_YOMITOKU_IGNORE_LINE_BREAK = "false"
      OCR_YOMITOKU_NO_FIGURE       = "false"
      OCR_YOMITOKU_FIGURE_WIDTH    = "200"
      OCR_YOMITOKU_FIGURE_DIR      = "figures"
    }
  }
  service_resources = var.cloudrun_service_resources
  secret_env_vars = {}
  service_secret_env_vars = {
    web    = var.cloudrun_secret_env_vars
    worker = var.cloudrun_secret_env_vars
  }
  cloudsql_instances = [module.cloudsql.connection_name]
  depends_on = [module.apis, module.secrets, module.cloudsql]
}

module "pubsub" {
  source            = "../../modules/pubsub"
  project_id        = var.project_id
  topic_name        = local.pubsub_topic_name
  subscription_name = local.pubsub_subscription_name
  push_endpoint     = "${local.worker_url}${local.pubsub_push_path}"
  push_sa_email     = module.cloudrun.service_accounts["worker"]
  topic_publisher_members = [
    "serviceAccount:gmail-api-push@system.gserviceaccount.com",
  ]
  depends_on        = [module.apis]
}

module "scheduler" {
  source          = "../../modules/scheduler"
  project_id      = var.project_id
  region          = var.region
  job_name        = local.watch_job_name
  schedule        = var.gmail_watch_schedule
  description     = "Gmail watch refresh"
  target_url      = "${local.worker_url}${local.watch_path}"
  target_sa_email = module.cloudrun.service_accounts["worker"]
  depends_on      = [module.apis]
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

module "gmail_scan_scheduler" {
  source          = "../../modules/scheduler"
  project_id      = var.project_id
  region          = var.region
  job_name        = "gmail-scan-${local.env}"
  schedule        = "*/5 * * * *"
  description     = "Scan Gmail for PDF attachments"
  target_url      = "${local.worker_url}/ingest/gmail-scan"
  target_sa_email = module.cloudrun.service_accounts["worker"]
  depends_on      = [module.apis]
}

module "iam" {
  source     = "../../modules/iam"
  project_id = var.project_id
  run_invoker_bindings = [
    {
      member  = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      service = local.worker_service_name
      location = var.region
    },
    {
      member  = "serviceAccount:${module.cloudrun.service_accounts["ocr-pipeline"]}"
      service = local.ocr_pipeline_service_name
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
      member = "serviceAccount:${module.cloudrun.service_accounts["web"]}"
      role   = "roles/secretmanager.secretAccessor"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      role   = "roles/secretmanager.secretAccessor"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      role   = "roles/datastore.user"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["web"]}"
      role   = "roles/cloudsql.client"
    },
    {
      member = "serviceAccount:${module.cloudrun.service_accounts["worker"]}"
      role   = "roles/cloudsql.client"
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

data "google_project" "current" {
  project_id = var.project_id
}

# Cloud Scheduler needs this permission to mint OIDC tokens for the worker service account.
resource "google_service_account_iam_member" "cloudscheduler_worker_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.cloudrun.service_accounts["worker"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
}

resource "google_eventarc_trigger" "ocr_pipeline_gcs" {
  name     = "ocr-pipeline-${local.env}"
  location = var.region
  project  = var.project_id

  service_account = module.cloudrun.service_accounts["ocr-pipeline"]

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = module.storage.raw_bucket
  }

  destination {
    cloud_run_service {
      service = local.ocr_pipeline_service_name
      region  = var.region
      path    = "/"
    }
  }

  depends_on = [module.apis, module.cloudrun, module.storage, module.iam]
}

module "monitoring" {
  source     = "../../modules/monitoring"
  project_id = var.project_id
  env        = local.env
  region     = var.region
  worker_service_name       = local.worker_service_name
  pubsub_subscription_name  = local.pubsub_subscription_name
  notification_emails       = var.notification_emails
  notification_channels      = var.notification_channels
  depends_on = [module.apis]
}
