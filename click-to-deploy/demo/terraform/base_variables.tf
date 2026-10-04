# The eight variables every go/demos click-to-deploy configuration must accept.
# The deployment runner supplies all of them; do not rename or remove any.
# Reconcile this file against the Sample_Standard_Demos template
# (cloud-gtm/data-cloud) before raising the PR -- the runner's contract wins.

variable "project_id" {
  description = "The Google Cloud project ID where resources are deployed."
  type        = string
}

variable "project_name" {
  description = "The display name of the demo project."
  type        = string
}

variable "project_number" {
  description = "The numeric ID of the demo project."
  type        = string
}

variable "org_id" {
  description = "The Organization ID where the project resides."
  type        = string
}

variable "gcp_account_name" {
  description = "The Argolis user (admin@<ldap>.altostrat.com) receiving permissions."
  type        = string
}

variable "deployment_service_account_name" {
  description = "The identity used by the runner to deploy resources."
  type        = string
}

variable "data_location" {
  description = "Path to central storage buckets for large demo assets."
  type        = string
}

variable "secret_stored_project" {
  description = "Project housing demo-specific secrets (passwords, keys)."
  type        = string
}
