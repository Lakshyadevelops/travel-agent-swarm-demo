# Cloud SQL for PostgreSQL: the durable arm.
#
# Small on purpose (db-g1-small, shared core): the question the demo asks is
# "small Postgres vs small Valkey on comparable hardware". Private Service
# Connect only, no public IP. Stock configuration except max_connections,
# which is sized for run_max_instances * postgres_pool_max.

resource "random_id" "postgres_suffix" {
  # Cloud SQL reserves a deleted instance's name for about a week; a suffix
  # lets a torn-down demo be redeployed immediately.
  byte_length = 2
}

resource "random_password" "postgres" {
  length  = 24
  special = false # the password travels inside a URL-form DSN
}

resource "google_sql_database_instance" "postgres" {
  project             = var.project_id
  name                = "${var.name_prefix}-pg-${random_id.postgres_suffix.hex}"
  region              = var.region
  database_version    = "POSTGRES_17"
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.postgres_tier
    edition           = "ENTERPRISE" # shared-core tiers exist only in this edition
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    disk_autoresize   = true
    user_labels       = var.labels

    deletion_protection_enabled = var.deletion_protection

    ip_configuration {
      ipv4_enabled = false
      # Encrypted when the client asks (asyncpg's default sslmode=prefer does),
      # without pinning a CA the demo would have to distribute.
      ssl_mode = "ALLOW_UNENCRYPTED_AND_ENCRYPTED"

      psc_config {
        psc_enabled               = true
        allowed_consumer_projects = [var.project_id]
      }
    }

    database_flags {
      name  = "max_connections"
      value = tostring(var.postgres_max_connections)
    }

    backup_configuration {
      enabled = false # sessions and scratchpads are ephemeral demo data
    }

    insights_config {
      query_insights_enabled = true # free, and handy when showing "under the hood"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "swarm" {
  project         = var.project_id
  instance        = google_sql_database_instance.postgres.name
  name            = "swarm"
  deletion_policy = "ABANDON" # the instance takes it down; avoids owner-dependency errors on destroy
}

resource "google_sql_user" "swarm" {
  project         = var.project_id
  instance        = google_sql_database_instance.postgres.name
  name            = "swarm"
  password        = random_password.postgres.result
  deletion_policy = "ABANDON"
}

# The app reads one DSN, delivered by Secret Manager so it never appears in the
# Cloud Run configuration or logs. Schema creation is the app's job at startup
# (app/state/schema.sql is idempotent).
resource "google_secret_manager_secret" "postgres_dsn" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-postgres-dsn"
  labels    = var.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "postgres_dsn" {
  secret = google_secret_manager_secret.postgres_dsn.id
  secret_data = format(
    "postgresql://%s:%s@%s:5432/%s",
    google_sql_user.swarm.name,
    random_password.postgres.result,
    google_compute_address.postgres.address,
    google_sql_database.swarm.name,
  )
}
