# ---------------------------------------------------------------- where
variable "project_id" {
  description = "Project that receives every resource."
  type        = string
}

variable "project_number" {
  description = "Numeric id of project_id (service agent e-mails are derived from it)."
  type        = string
}

variable "region" {
  description = "Region for the VPC subnet, Cloud Run, Cloud SQL and Memorystore."
  type        = string
  default     = "us-west1"
}

variable "zone" {
  description = "Zone for the single-zone Memorystore instance (must be in region)."
  type        = string
  default     = "us-west1-b"
}

variable "name_prefix" {
  description = "Prefix for every resource name."
  type        = string
  default     = "travel-swarm"
}

variable "subnet_cidr" {
  description = "Private range for Cloud Run egress and the two store endpoints."
  type        = string
  default     = "10.10.0.0/24"
}

variable "enable_apis" {
  description = "Enable the required Google APIs from this module (set false if the project already manages them)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- app
variable "image" {
  description = "Fully qualified container image for the demo server, e.g. us-west1-docker.pkg.dev/<project>/travel-swarm/app:<tag>."
  type        = string
}

variable "gemini_model" {
  description = "Gemini model id the agents call through Vertex AI."
  type        = string
  default     = "gemini-3.6-flash"
}

variable "vertex_location" {
  description = "Vertex AI location for Gemini. Gemini 3.x publisher models are served from \"global\"; a region returns 404."
  type        = string
  default     = "global"
}

variable "run_min_instances" {
  description = "Warm instances kept around. 1 keeps the first visitor from paying a cold start."
  type        = number
  default     = 1
}

variable "run_max_instances" {
  type    = number
  default = 10
}

variable "run_concurrency" {
  description = "Requests per Cloud Run instance. Each planning run holds one streaming request."
  type        = number
  default     = 40
}

# ---------------------------------------------------------------- access
variable "iap_members" {
  description = "IAM members allowed through Identity-Aware Proxy, e.g. [\"group:everyone@google.com\"] or [\"user:ldap@google.com\"]."
  type        = list(string)
}

variable "enable_iap" {
  description = "Put the Cloud Run URL behind IAP (recommended; the demo has no login of its own)."
  type        = bool
  default     = true
}

# Optional external HTTPS load balancer with a Google-managed certificate, for
# a vanity hostname (go/demos hosting route 2). IAP then sits on the backend
# service and Cloud Run only accepts traffic from the load balancer.
variable "enable_load_balancer" {
  type    = bool
  default = false
}

variable "demo_domain" {
  description = "Hostname for the managed certificate when enable_load_balancer is true."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------- stores
variable "postgres_tier" {
  description = "Cloud SQL machine tier. db-g1-small (shared core, 1.7 GB) matches the \"small instance\" question the demo asks."
  type        = string
  default     = "db-g1-small"
}

variable "postgres_max_connections" {
  description = "Server-side cap. Keep >= run_max_instances * postgres_pool_max + headroom."
  type        = number
  default     = 100
}

variable "postgres_pool_max" {
  description = "asyncpg pool size per Cloud Run instance (also the per-instance concurrency gate)."
  type        = number
  default     = 8
}

variable "valkey_node_type" {
  description = "Memorystore node type. SHARED_CORE_NANO (1.4 GB) is the smallest and the fair peer of db-g1-small."
  type        = string
  default     = "SHARED_CORE_NANO"
}

variable "valkey_engine_version" {
  type    = string
  default = "VALKEY_9_1"
}

variable "deletion_protection" {
  description = "Protect the stores from terraform destroy. Off for a demo."
  type        = bool
  default     = false
}

variable "labels" {
  type = map(string)
  default = {
    app = "travel-swarm"
  }
}
