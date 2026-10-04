# Travel Concierge Swarm -- click-to-deploy orchestration layer.
#
# Deploys the whole demo into the Argolis project the go/demos runner hands us:
# a private VPC, Memorystore for Valkey and Cloud SQL for PostgreSQL as PSC
# endpoints, and the demo server on Cloud Run calling Gemini through Vertex AI,
# with the deploying user admitted through Identity-Aware Proxy.
#
# Everything is in terraform-modules/travel_swarm; this file only wires the
# runner's inputs to it. See ../../README.md.

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

module "travel_swarm" {
  source = "./terraform-modules/travel_swarm"

  project_id     = var.project_id
  project_number = var.project_number
  region         = var.region
  zone           = var.zone

  image           = var.image
  gemini_model    = var.gemini_model
  vertex_location = var.vertex_location

  # The person who clicked Deploy can open the demo; add teammates via
  # extra_iap_members.
  iap_members = concat(["user:${var.gcp_account_name}"], var.extra_iap_members)

  labels = {
    app         = "travel-swarm"
    demo        = "travel-concierge-swarm"
    environment = "click-to-deploy"
  }
}
