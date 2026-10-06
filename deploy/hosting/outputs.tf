output "demo_url" {
  value = module.travel_swarm.service_url
}

output "image" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${var.registry_repository}/app:${var.image_tag}"
}

output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${var.registry_repository}"
}

output "build_service_account" {
  value = google_service_account.build.email
}

output "run_service_account" {
  value = module.travel_swarm.run_service_account
}

output "smoke_service_account" {
  value = google_service_account.smoke.email
}

output "valkey_host" {
  value = module.travel_swarm.valkey_host
}

output "postgres_host" {
  value = module.travel_swarm.postgres_host
}

output "postgres_instance" {
  value = module.travel_swarm.postgres_instance
}

output "postgres_dsn_secret" {
  value = module.travel_swarm.postgres_dsn_secret
}

output "load_balancer_ip" {
  value = module.travel_swarm.load_balancer_ip
}
