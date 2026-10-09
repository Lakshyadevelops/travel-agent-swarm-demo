output "demo_url" {
  description = "Open this (IAP sign-in with the deploying Argolis account)."
  value       = module.travel_swarm.service_url
}

output "valkey_host" {
  value = module.travel_swarm.valkey_host
}

output "postgres_host" {
  value = module.travel_swarm.postgres_host
}

output "postgres_dsn_secret" {
  value = module.travel_swarm.postgres_dsn_secret
}

output "run_service_account" {
  value = module.travel_swarm.run_service_account
}

output "image" {
  description = "Image Cloud Run is serving (built in this project unless var.image was set)."
  value       = module.travel_swarm.image
}
