# Optional: external HTTPS load balancer with a Google-managed certificate, for
# a vanity hostname such as demo-<id>.<...>.demo.altostrat.com (go/demos
# hosting route 2). Off by default; the run.app URL behind Cloud Run IAP is
# enough for a 24x7 link. When on, IAP moves to the backend service and Cloud
# Run only accepts traffic that came through the load balancer.

locals {
  lb_count = var.enable_load_balancer ? 1 : 0
}

resource "google_compute_global_address" "lb" {
  count = local.lb_count

  project = var.project_id
  name    = "${var.name_prefix}-lb"
}

resource "google_compute_region_network_endpoint_group" "run" {
  count = local.lb_count

  project               = var.project_id
  name                  = "${var.name_prefix}-run-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }
}

resource "google_compute_backend_service" "run" {
  count = local.lb_count

  project               = var.project_id
  name                  = "${var.name_prefix}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.run[0].id
  }

  iap {
    enabled = var.enable_iap
  }
}

resource "google_compute_url_map" "lb" {
  count = local.lb_count

  project         = var.project_id
  name            = "${var.name_prefix}-lb"
  default_service = google_compute_backend_service.run[0].id
}

resource "google_compute_managed_ssl_certificate" "lb" {
  count = local.lb_count

  project = var.project_id
  name    = "${var.name_prefix}-cert"

  managed {
    domains = [var.demo_domain]
  }
}

resource "google_compute_target_https_proxy" "lb" {
  count = local.lb_count

  project          = var.project_id
  name             = "${var.name_prefix}-https"
  url_map          = google_compute_url_map.lb[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.lb[0].id]
}

resource "google_compute_global_forwarding_rule" "lb" {
  count = local.lb_count

  project               = var.project_id
  name                  = "${var.name_prefix}-https"
  target                = google_compute_target_https_proxy.lb[0].id
  ip_address            = google_compute_global_address.lb[0].address
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# IAP access for people is the project-level grant in iam.tf; it applies to
# this backend service as well as to the run.app URL.
