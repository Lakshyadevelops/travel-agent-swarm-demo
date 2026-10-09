# Terraform 1.3 is the floor because the go/demos click-to-deploy runner image
# ships Terraform 1.3.7; nothing here needs anything newer (no terraform_data,
# no check blocks, no import blocks).
terraform {
  required_version = ">= 1.3"

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
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2, < 4.0"
    }
  }
}
