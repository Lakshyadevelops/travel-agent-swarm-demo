# APIs the stack needs. disable_on_destroy = false: tearing the demo down must
# not switch off APIs that something else in the project may rely on.
locals {
  services = [
    "cloudresourcemanager.googleapis.com", # project IAM bindings
    "iam.googleapis.com",
    "compute.googleapis.com",
    "networkconnectivity.googleapis.com", # service connection policy for Memorystore PSC
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "memorystore.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "iap.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = var.enable_apis ? toset(local.services) : toset([])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
