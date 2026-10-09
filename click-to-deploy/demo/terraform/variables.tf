# Demo-specific inputs (the mandatory runner inputs live in base_variables.tf).

variable "image" {
  description = <<-EOT
    Leave empty (the default): the demo image is built by Cloud Build inside the
    Argolis project from this repository, so a fresh project needs no access to
    anyone else's registry. Set a full image reference only to skip the build
    with an image that project can already pull.
  EOT
  type        = string
  default     = ""
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
