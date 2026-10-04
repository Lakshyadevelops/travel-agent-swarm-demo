# Demo-specific inputs (the mandatory runner inputs live in base_variables.tf).

variable "image" {
  description = "Pre-built demo image. Published from the maintainer's project; see click-to-deploy/README.md for how the Argolis project is granted pull access."
  type        = string
  default     = "us-west1-docker.pkg.dev/ks-opm-project/travel-swarm/app:latest"
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "zone" {
  type    = string
  default = "us-west1-b"
}

variable "vertex_location" {
  description = "Gemini 3.x publisher models are served from \"global\"."
  type        = string
  default     = "global"
}

variable "gemini_model" {
  type    = string
  default = "gemini-3.6-flash"
}

variable "extra_iap_members" {
  description = "Additional IAM members admitted by IAP besides the deploying Argolis user, e.g. [\"domain:altostrat.com\"]."
  type        = list(string)
  default     = []
}
