# Travel Concierge Swarm — click-to-deploy

Terraform for the go/demos click-to-deploy runner. It builds the whole demo in
the Argolis project the runner provides:

| Piece | Resource | Why this shape |
|---|---|---|
| Network | VPC `travel-swarm-vpc`, subnet `10.10.0.0/24`, Private Google Access | Everything private; no VM, no public IP |
| In-memory arm | Memorystore for Valkey 9.1, `SHARED_CORE_NANO`, 1 shard, 0 replicas, cluster mode off, `allkeys-lru` | Smallest managed Valkey; cluster mode off because the app uses multi-key pipelines |
| Durable arm | Cloud SQL for PostgreSQL 17, `db-g1-small`, zonal, `max_connections=100`, PSC only | Smallest managed Postgres; the "small vs small" comparison the demo makes |
| Connectivity | Private Service Connect endpoints for both stores | No VPC peering (blocked by `compute.restrictVpcPeering` in many orgs) |
| App | Cloud Run v2, 1 vCPU / 1 GiB, min 1 / max 10 instances, Direct VPC egress | Same image as the maintainer's hosted instance |
| Model | Gemini (`gemini-3.6-flash`) through Vertex AI, location `global`, service-account ADC | No API key anywhere in the deployment |
| Secrets | Postgres DSN in Secret Manager, mounted as an env var | Credentials never in the Cloud Run config |
| Access | Cloud Run IAP; `roles/iap.httpsResourceAccessor` for `user:<gcp_account_name>` | The deploying Argolis user opens the URL and signs in |

Layout follows the [Demo Automation Guide](https://g3doc.corp.google.com/company/teams/godemos/demoautomation.md):

```
click-to-deploy/
  demo/terraform/
    main.tf                 orchestration: wires runner inputs into the module
    base_variables.tf       the 8 mandatory runner variables
    variables.tf            demo inputs (image, region, model)
    outputs.tf
    terraform-modules/travel_swarm/   the stack (also used by deploy/hosting)
  org_policy/
    project_config.json     required APIs + the one org-policy relaxation
    project_resource.tf     (copy from the template; do not edit)
```

## Before raising the PR

Two files come from the private `Sample_Standard_Demos` template in
`cloud-gtm/data-cloud` and could not be read from this environment:

1. `org_policy/project_resource.tf` — copy it verbatim from the template.
2. `org_policy/project_config.json` — the schema (key names) must match what
   `project_resource.tf` reads. The content here lists what this demo needs:
   the APIs above and `constraints/run.allowedIngress = allowAll` (Cloud Run
   IAP requires ingress "all"; access is gated by IAP, not by network), with
   restore-after-deployment on. Adjust key names to the template.

Also check `base_variables.tf` against the template's copy; names and types
must match the runner's contract exactly.

## The image

The runner does not build containers, so Cloud Run pulls a pre-built image
from the maintainer's registry:

```
us-west1-docker.pkg.dev/ks-opm-project/travel-swarm/app:<tag>
```

The Argolis project's Cloud Run service agent
(`service-<PROJECT_NUMBER>@serverless-robot-prod.iam.gserviceaccount.com`)
needs `roles/artifactregistry.reader` on that repository. The maintainer grants
it through `image_readers` in `deploy/hosting` (one entry per consuming
project). If the google.com organisation's domain-restricted-sharing policy
rejects the cross-organisation grant, the fallback is to build from source in
the Argolis project (`cloudbuild.yaml` at the repository root) and point
`var.image` at that registry.

## Deploying by hand (to test the configuration)

```bash
cd click-to-deploy/demo/terraform
terraform init
terraform apply \
  -var project_id=<argolis-project> -var project_number=<number> \
  -var project_name=travel-swarm -var org_id=<org> \
  -var gcp_account_name=admin@<ldap>.altostrat.com \
  -var deployment_service_account_name=<runner-sa> \
  -var data_location=gs://unused -var secret_stored_project=<argolis-project>
```

First apply takes 15–20 minutes (Cloud SQL and Memorystore provisioning
dominate). `terraform output demo_url` prints the IAP-protected link.

## Cost (list prices, us-west1)

Memorystore `SHARED_CORE_NANO` ≈ \$23/month, Cloud SQL `db-g1-small` ≈ \$25/month
plus 10 GB SSD ≈ \$2, Cloud Run with one warm instance ≈ \$5–15/month depending
on traffic. Roughly \$55–65/month idle.

## Teardown

`terraform destroy` removes everything, including the stores (deletion
protection is off for the demo). APIs stay enabled.
