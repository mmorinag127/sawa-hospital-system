output "raw_bucket" {
  value = module.storage.raw_bucket
}

output "templates_bucket" {
  value = module.storage.templates_bucket
}

output "exports_bucket" {
  value = module.storage.exports_bucket
}

output "cloudrun_urls" {
  value = module.cloudrun.service_urls
}

output "cloudrun_exec_sas" {
  value = module.cloudrun.service_accounts
}

output "pubsub_topic" {
  value = module.pubsub.topic
}

output "pubsub_subscription" {
  value = module.pubsub.subscription
}

output "ingest_retry_scheduler_job" {
  value = module.ingest_retry_scheduler.job_name
}

output "cloudsql_connection_name" {
  value = module.cloudsql.connection_name
}
