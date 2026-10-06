variable "project_id" {
  type    = string
  default = "ks-opm-project"
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "zone" {
  type    = string
  default = "us-west1-b"
}

variable "registry_repository" {
  description = "Artifact Registry repository id for the demo image."
  type        = string
  default     = "travel-swarm"
}

variable "image_tag" {
  description = "Tag of the image to run (scripts/deploy_hosting.sh sets it to the short commit)."
  type        = string
  default     = "latest"
}

variable "image_readers" {
  description = "Extra principals allowed to pull the image, e.g. a click-to-deploy project's Cloud Run service agent."
  type        = list(string)
  default     = []
}

variable "operators" {
  description = "People who run deploys; they may mint tokens for the smoke-test service account that calls the hosted URL through IAP."
  type        = list(string)
  default     = ["user:lakshyagg@google.com"]
}

variable "gemini_model" {
  type    = string
  default = "gemini-3.6-flash"
}

variable "vertex_location" {
  type    = string
  default = "global"
}

variable "iap_members" {
  description = "Who may open the hosted demo. go/demos asks for the everyone@google.com group; fall back to user:<ldap>@google.com if Domain Restricted Sharing rejects it."
  type        = list(string)
  default     = ["group:everyone@google.com"]
}

variable "enable_load_balancer" {
  type    = bool
  default = false
}

variable "demo_domain" {
  type    = string
  default = ""
}

variable "run_min_instances" {
  type    = number
  default = 1
}

variable "run_max_instances" {
  type    = number
  default = 10
}
