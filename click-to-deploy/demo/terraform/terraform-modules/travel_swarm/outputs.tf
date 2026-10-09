output "service_url" {
  description = "The demo. Behind IAP when enable_iap is true."
  value       = var.enable_load_balancer ? "https://${var.demo_domain}" : google_cloud_run_v2_service.app.uri
}

output "run_service_url" {
  description = "Cloud Run's own URL (closed to direct traffic when the load balancer is on)."
  value       = google_cloud_run_v2_service.app.uri
}

output "run_service_name" {
  value = google_cloud_run_v2_service.app.name
}

output "run_service_account" {
  value = google_service_account.run.email
}

output "valkey_host" {
  description = "PSC endpoint IP of the Memorystore for Valkey instance (private)."
  value       = local.valkey_host
}

output "valkey_instance" {
  value = google_memorystore_instance.valkey.id
}

output "postgres_host" {
  description = "PSC endpoint IP of the Cloud SQL instance (private)."
  value       = google_compute_address.postgres.address
}

output "postgres_instance" {
  value = google_sql_database_instance.postgres.name
}

output "postgres_dsn_secret" {
  description = "Secret Manager id holding the full DSN."
  value       = google_secret_manager_secret.postgres_dsn.secret_id
}

output "load_balancer_ip" {
  description = "Point demo_domain's A record here when enable_load_balancer is true."
  value       = var.enable_load_balancer ? google_compute_global_address.lb[0].address : null
}

output "network" {
  value = google_compute_network.vpc.id
}

output "image" {
  description = "Image the service runs (built in-project when var.image was empty)."
  value       = local.image
}
