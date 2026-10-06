# The 24x7 hosted demo in ks-opm-project (the go/demos live link).
#
# Same module as click-to-deploy, plus what only the maintainer's project
# needs: the Artifact Registry repository the image is published to, and a
# build identity for Cloud Build. Apply with scripts/deploy_hosting.sh.

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 8.5, < 9.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 8.5, < 9.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {
  project_id = var.project_id
}

# ---------------------------------------------------------- image registry
resource "google_project_service" "registry" {
  for_each = toset(["artifactregistry.googleapis.com", "cloudbuild.googleapis.com"])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  project       = var.project_id
  location      = var.region
  repository_id = var.registry_repository
  format        = "DOCKER"
  description   = "Travel Concierge Swarm demo images"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "drop-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s" # 30 days
    }
  }

  depends_on = [google_project_service.registry]
}

# Cloud Build runs as this account (scripts/deploy_hosting.sh --cloud-build).
# A dedicated identity instead of the Compute Engine default, which the
# google.com organisation leaves without roles.
resource "google_service_account" "build" {
  project      = var.project_id
  account_id   = "travel-swarm-build"
  display_name = "Travel Concierge Swarm (Cloud Build)"
}

# Project-level like the module's grants: the project holds one repository and
# the applier needs only roles/resourcemanager.projectIamAdmin.
resource "google_project_iam_member" "build_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = google_service_account.build.member
}

resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.build.member
}

resource "google_project_iam_member" "build_source" {
  project = var.project_id
  role    = "roles/storage.objectViewer" # reads the staged source tarball
  member  = google_service_account.build.member
}

# Other projects that may pull the image (for example the go/demos
# click-to-deploy target). Empty by default; see click-to-deploy/README.md.
resource "google_project_iam_member" "readers" {
  for_each = toset(var.image_readers)

  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = each.value
}

# ---------------------------------------------------------------- smoke tests
# IAP admits a service-account ID token minted for its OAuth client id; a
# user's gcloud identity token cannot carry that audience. The deploy script
# impersonates this account (no key) to call the hosted URL after an apply.
resource "google_service_account" "smoke" {
  project      = var.project_id
  account_id   = "travel-swarm-smoke"
  display_name = "Travel Concierge Swarm (post-deploy checks through IAP)"
}

resource "google_project_iam_member" "smoke_iap" {
  project = var.project_id
  role    = "roles/iap.httpsResourceAccessor"
  member  = google_service_account.smoke.member
}

resource "google_service_account_iam_member" "smoke_impersonators" {
  for_each = toset(var.operators)

  service_account_id = google_service_account.smoke.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

# ---------------------------------------------------------------- the demo
module "travel_swarm" {
  source = "../../click-to-deploy/demo/terraform/terraform-modules/travel_swarm"

  project_id     = var.project_id
  project_number = data.google_project.this.number
  region         = var.region
  zone           = var.zone

  image           = "${var.region}-docker.pkg.dev/${var.project_id}/${var.registry_repository}/app:${var.image_tag}"
  gemini_model    = var.gemini_model
  vertex_location = var.vertex_location

  iap_members          = var.iap_members
  enable_load_balancer = var.enable_load_balancer
  demo_domain          = var.demo_domain

  run_min_instances = var.run_min_instances
  run_max_instances = var.run_max_instances

  labels = {
    app         = "travel-swarm"
    demo        = "travel-concierge-swarm"
    environment = "hosting"
  }

  depends_on = [google_artifact_registry_repository.app]
}
