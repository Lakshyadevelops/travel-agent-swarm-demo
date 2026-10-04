# Memorystore for Valkey: the in-memory arm.
#
# Shape mirrors the local benchmark container as closely as a managed service
# allows: one node, no replica, allkeys-lru eviction, stock everything else.
# CLUSTER_DISABLED is required: the app uses multi-key pipelines (HASH + ZSET
# per session) that a cluster-mode keyspace would reject. No AUTH / TLS is a
# deliberate trade: the endpoint exists only inside the private subnet and
# the app would otherwise need to carry a credential for a cache of demo data.

resource "google_memorystore_instance" "valkey" {
  project     = var.project_id
  instance_id = "${var.name_prefix}-valkey"
  location    = var.region

  shard_count    = 1
  replica_count  = 0
  node_type      = var.valkey_node_type
  engine_version = var.valkey_engine_version
  mode           = "CLUSTER_DISABLED"

  authorization_mode      = "AUTH_DISABLED"
  transit_encryption_mode = "TRANSIT_ENCRYPTION_DISABLED"

  engine_configs = {
    maxmemory-policy = "allkeys-lru"
  }

  zone_distribution_config {
    mode = "SINGLE_ZONE"
    zone = var.zone
  }

  persistence_config {
    mode = "DISABLED" # the arm being priced is "no durability"
  }

  desired_auto_created_endpoints {
    network    = google_compute_network.vpc.id
    project_id = var.project_id
  }

  deletion_protection_enabled = var.deletion_protection
  labels                      = var.labels

  depends_on = [google_network_connectivity_service_connection_policy.memorystore]
}

# The consumer-side IP of the auto-created PSC endpoint. A CLUSTER_DISABLED
# instance exposes a PRIMARY connection (and a READER when replicas exist);
# the app talks to the primary.
locals {
  valkey_connections = flatten([
    for endpoint in google_memorystore_instance.valkey.endpoints : [
      for connection in endpoint.connections : connection.psc_auto_connection[0]
      if length(connection.psc_auto_connection) > 0
    ]
  ])
  valkey_primary_ips = [
    for c in local.valkey_connections : c.ip_address
    if c.connection_type == "CONNECTION_TYPE_PRIMARY"
  ]
  valkey_host = (
    length(local.valkey_primary_ips) > 0
    ? local.valkey_primary_ips[0]
    : local.valkey_connections[0].ip_address
  )
  valkey_url = "redis://${local.valkey_host}:6379"
}
