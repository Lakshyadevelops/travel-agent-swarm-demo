# The eight variables every go/demos click-to-deploy configuration must accept.
# The runner passes all of them on `terraform apply` (see the platform's
# cloud_build.json: data_location, project_id, project_name, gcp_account_name,
# deployment_service_account_name, org_id, project_number, secret_stored_project).
# Do not rename or remove any. The ones this demo does not read keep an empty
# default so a hand-run `terraform plan` needs only the values that matter.

variable "project_id" {
  description = "The Google Cloud project ID where resources are deployed."
  type        = string
}

variable "project_name" {
  description = "The display name of the demo project."
  type        = string
  default     = ""
}

variable "project_number" {
  description = "The numeric ID of the demo project (the runner reads it from the project it just created)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.project_number))
    error_message = "project_number must be the numeric project number."
  }
}

variable "org_id" {
  description = "The Organization ID where the project resides."
  type        = string
  default     = ""
}

variable "gcp_account_name" {
  description = "The Argolis user (admin@<ldap>.altostrat.com) who clicked Deploy; admitted through IAP."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+$", var.gcp_account_name))
    error_message = "gcp_account_name must be an e-mail address (the Argolis admin account)."
  }
}

variable "deployment_service_account_name" {
  description = "The identity used by the runner to deploy resources."
  type        = string
  default     = ""
}

variable "data_location" {
  description = "Path under the central demo-assets bucket for large demo files (unused: this demo ships no data)."
  type        = string
  default     = ""
}

variable "secret_stored_project" {
  description = "Project housing platform-managed secrets (unused: the only secret, the Postgres DSN, is generated and stored in project_id)."
  type        = string
  default     = ""
}
