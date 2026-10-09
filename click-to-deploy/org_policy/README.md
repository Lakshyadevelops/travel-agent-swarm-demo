# org_policy/

The go/demos runner applies this directory with Terraform right after it
creates the Argolis project (`terraform apply -var project_id=<id>`), waits two
minutes for the policies to settle, then deploys `../demo/terraform`. After the
deployment the platform deletes every project-level policy listed in
`project_config.json` with `"restore_post_deployment": true`, which puts the
project back to the Argolis defaults.

Two files belong here:

| File | Status |
|---|---|
| `project_config.json` | Ours. Shape is what the platform parses: `constraints[].constraint` (bare constraint name, no `constraints/` prefix) and `constraints[].restore_post_deployment`. |
| `project_resource.tf` | **Copy verbatim** from `Sample_Standard_Demos/org_policy/project_resource.tf` in [cloud-gtm/data-cloud](https://github.com/cloud-gtm/data-cloud/tree/master/Sample_Standard_Demos/org_policy). Do not edit it; it is the platform's reader for the JSON. (It is not in this repository because that template is only reachable with a cloud-gtm GitHub login.) If the template's `project_config.json` carries extra keys that `project_resource.tf` reads, keep those and set the constraints list to the one here. |

## Why `run.allowedIngress`

Cloud Run's built-in Identity-Aware Proxy needs the service to accept all
ingress; access is gated by IAP (`roles/iap.httpsResourceAccessor` for the
deploying user), not by network. Argolis pins `run.allowedIngress` to
internal-only by default, so it is relaxed for the deployment and restored
afterwards -- the constraint is evaluated only when a service is created or
updated, so the running service is unaffected.

Nothing else in the stack conflicts with the Argolis defaults: no VM, no public
IP, no VPC peering (both stores are Private Service Connect endpoints), no
service-account key, Cloud SQL without a public address. The IAP grant goes to
`user:<gcp_account_name>`, which is inside the Argolis organisation, so
`iam.allowedPolicyMemberDomains` is satisfied.
