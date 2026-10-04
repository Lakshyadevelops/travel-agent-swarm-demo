# One private VPC. Cloud Run reaches the stores through Direct VPC egress; the
# stores are reachable only as Private Service Connect endpoints inside this
# subnet. Nothing here has a public IP.
#
# Why PSC for both stores and not Private Services Access for Cloud SQL: PSA
# needs a VPC peering to a Google producer network, which the google.com
# organisation policy compute.restrictVpcPeering forbids in this project. PSC
# is a forwarding rule in our own subnet, so it is not peering at all.

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "app" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-${var.region}"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true # Vertex AI, Secret Manager, logging: Google APIs over private access
}

# Lets Memorystore place its auto-created PSC endpoint in our subnet.
resource "google_network_connectivity_service_connection_policy" "memorystore" {
  project       = var.project_id
  name          = "${var.name_prefix}-memorystore"
  location      = var.region
  service_class = "gcp-memorystore"
  network       = google_compute_network.vpc.id

  psc_config {
    subnetworks = [google_compute_subnetwork.app.id]
  }

  depends_on = [google_project_service.apis]
}

# Cloud SQL's PSC endpoint: a reserved internal address plus a forwarding rule
# aimed at the instance's service attachment. The app connects to this IP.
resource "google_compute_address" "postgres" {
  project      = var.project_id
  name         = "${var.name_prefix}-postgres-psc"
  region       = var.region
  subnetwork   = google_compute_subnetwork.app.id
  address_type = "INTERNAL"
}

resource "google_compute_forwarding_rule" "postgres" {
  project               = var.project_id
  name                  = "${var.name_prefix}-postgres-psc"
  region                = var.region
  network               = google_compute_network.vpc.id
  ip_address            = google_compute_address.postgres.id
  target                = google_sql_database_instance.postgres.psc_service_attachment_link
  load_balancing_scheme = "" # PSC consumer endpoint, not a load balancer
}
