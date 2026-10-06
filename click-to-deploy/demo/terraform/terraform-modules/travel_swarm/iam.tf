# Identities and grants.
#
# The Cloud Run service runs as its own service account with exactly three
# permissions: call Vertex AI, read the one DSN secret, and (implicitly) write
# its own logs. Nothing in this module creates a service-account key.

resource "google_service_account" "run" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-run"
  display_name = "Travel Concierge Swarm (Cloud Run)"

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "run_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.run.member
}

# Grants are project-level on purpose. The project is single-purpose, and an
# applier holding roles/resourcemanager.projectIamAdmin can set them without
# the Secret Manager, Cloud Run and IAP admin roles that per-resource bindings
# need. One IAP binding also covers both routes (run.app URL or load balancer).
resource "google_project_iam_member" "run_postgres_dsn" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.run.member
}

# Identity-Aware Proxy. IAP's service agent must be allowed to invoke the
# service; people are then admitted per iap_members. No OAuth client of our
# own: Cloud Run IAP uses Google-managed OAuth.
resource "google_project_service_identity" "iap" {
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "iap_invoker" {
  count = var.enable_iap ? 1 : 0

  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_project_service_identity.iap.email}"
}

resource "google_project_iam_member" "iap_users" {
  for_each = var.enable_iap ? toset(var.iap_members) : toset([])

  project = var.project_id
  role    = "roles/iap.httpsResourceAccessor"
  member  = each.value
}
