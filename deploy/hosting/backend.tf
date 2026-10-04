# Remote state for the 24x7 hosted instance. The bucket is created once by
# scripts/deploy_hosting.sh (gcloud storage buckets create, versioning on).
terraform {
  backend "gcs" {
    bucket = "ks-opm-project-tfstate"
    prefix = "travel-swarm/hosting"
  }
}
