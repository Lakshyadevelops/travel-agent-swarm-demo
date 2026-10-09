# The demo server on Cloud Run.
#
# One container image, configured entirely through environment variables so
# the same image runs on a laptop against docker-compose stores. Gemini is
# reached through Vertex AI with the service account's credentials; the stores
# through Direct VPC egress to their PSC endpoints.

locals {
  # When the external load balancer fronts the service, IAP moves to the
  # backend service and Cloud Run stops answering its run.app URL directly.
  run_ingress     = var.enable_load_balancer ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"
  run_iap_enabled = var.enable_iap && !var.enable_load_balancer

  run_env = {
    LLM_MODE                  = "gemini"
    GEMINI_MODEL              = var.gemini_model
    GOOGLE_GENAI_USE_VERTEXAI = "true"
    GOOGLE_CLOUD_PROJECT      = var.project_id
    GOOGLE_CLOUD_LOCATION     = var.vertex_location
    VALKEY_URL                = local.valkey_url
    VALKEY_CACHE_URL          = "off" # no second Valkey: the UI never offers the cached arm
    RUNS_DIR                  = "/tmp/runs"
    # Per-instance Postgres budget: run_max_instances * pool_max must stay
    # under the server's max_connections (see postgres_max_connections).
    POSTGRES_POOL_MIN        = "1"
    POSTGRES_POOL_MAX        = tostring(var.postgres_pool_max)
    POSTGRES_MAX_CONCURRENCY = tostring(var.postgres_pool_max)
  }
}

resource "google_cloud_run_v2_service" "app" {
  project  = var.project_id
  name     = "${var.name_prefix}-app"
  location = var.region
  labels   = var.labels

  ingress             = local.run_ingress
  iap_enabled         = local.run_iap_enabled
  deletion_protection = false

  template {
    service_account                  = google_service_account.run.email
    timeout                          = "900s" # a planning run streams for a few minutes at most
    max_instance_request_concurrency = var.run_concurrency
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    labels                           = var.labels

    scaling {
      min_instance_count = var.run_min_instances
      max_instance_count = var.run_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY" # stores via the VPC; Google APIs straight out

      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.app.id
      }
    }

    containers {
      image = local.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.run_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "POSTGRES_DSN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.postgres_dsn.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 3
        period_seconds        = 3
        timeout_seconds       = 3
        failure_threshold     = 20

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  # Everything the container touches in its first second of life, and the
  # image itself when this module built it.
  depends_on = [
    null_resource.build,
    google_memorystore_instance.valkey,
    google_compute_forwarding_rule.postgres,
    google_sql_user.swarm,
    google_secret_manager_secret_version.postgres_dsn,
    google_project_iam_member.run_postgres_dsn,
    google_project_iam_member.run_vertex,
  ]
}
