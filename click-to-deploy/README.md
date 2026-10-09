# Travel Concierge Swarm — click-to-deploy

Terraform for the go/demos click-to-deploy runner. It builds the whole demo in
the Argolis project the runner provides, including the container image:

| Piece | Resource | Why this shape |
|---|---|---|
| Image | Cloud Build in the Argolis project → Artifact Registry `travel-swarm/app:<content hash>` | Every click lands in a brand-new project that cannot pull from anyone else's registry, so the image is built where it runs (`terraform-modules/travel_swarm/build.tf`) |
| Network | VPC `travel-swarm-vpc`, subnet `10.10.0.0/24`, Private Google Access | Everything private; no VM, no public IP |
| In-memory arm | Memorystore for Valkey 9.1, `SHARED_CORE_NANO`, 1 shard, 0 replicas, cluster mode off, `allkeys-lru` | Smallest managed Valkey; cluster mode off because the app uses multi-key pipelines |
| Durable arm | Cloud SQL for PostgreSQL 17, `db-g1-small`, zonal, `max_connections=100`, PSC only | Smallest managed Postgres; the "small vs small" comparison the demo makes |
| Connectivity | Private Service Connect endpoints for both stores | No VPC peering (blocked by `compute.restrictVpcPeering` in Argolis) |
| App | Cloud Run v2, 1 vCPU / 1 GiB, min 1 / max 10 instances, Direct VPC egress | Same module as the maintainer's hosted instance (`deploy/hosting`) |
| Model | Gemini (`gemini-3.6-flash`) through Vertex AI, location `global`, service-account ADC | No API key anywhere in the deployment |
| Secrets | Postgres DSN (random password) in Secret Manager, mounted as an env var | Credentials never in the Cloud Run config or the repo |
| Access | Cloud Run IAP; `roles/iap.httpsResourceAccessor` for `user:<gcp_account_name>` | The deploying Argolis user opens the URL and signs in |

## How the runner drives this directory

Facts from the platform's own build definition
(`google3/cloud/solutions/argolis_cloud_environments/libraries/builds/cloud_build.json`
and `worker_orgpolicy`), which is why the files look the way they do:

1. A Cloud Build in the CDP project clones the dedicated demo repository and
   runs every Terraform step in the `ctd-demo-builder` image, which ships
   **Terraform 1.3.7** and the Cloud SDK. Hence `required_version = ">= 1.3"`,
   `null_resource` rather than `terraform_data`, and `gcloud builds submit` for
   the image.
2. It writes a `backend.tf` into each Terraform folder before `terraform init`
   — so neither root here declares a backend.
3. It creates the Argolis project, then applies `org_policy/` with
   `-var project_id=<id>`, waits 120 s, then applies `demo/terraform/` with
   exactly the eight variables in `base_variables.tf` (`data_location`,
   `project_id`, `project_name`, `gcp_account_name`,
   `deployment_service_account_name`, `org_id`, `project_number`,
   `secret_stored_project` — the last one is the **CDP** project, not the Argolis
   one, which is why the DSN secret lives in `project_id`).
4. After the deployment it deletes every project-level policy listed in
   `org_policy/project_config.json` with `"restore_post_deployment": true`.

## Layout

```
click-to-deploy/
  demo/terraform/
    main.tf                 orchestration: wires runner inputs into the module
    base_variables.tf       the 8 mandatory runner variables
    variables.tf            demo inputs (region, model, optional prebuilt image)
    outputs.tf              demo_url, image, store hosts
    terraform-modules/travel_swarm/   the stack (also used by deploy/hosting)
  org_policy/
    project_config.json     the one constraint relaxed for the deployment, restored afterwards
    project_resource.tf     copy verbatim from the Sample_Standard_Demos template (see org_policy/README.md)
```

The build needs the repository root (`Dockerfile`, `cloudbuild.yaml`, `app/`)
three directories above `demo/terraform`, so the dedicated cloud-gtm repository
must hold the whole application, not just this folder. `.gcloudignore` at the
root keeps the upload to the files the image needs.

## Submitting to go/demos

Steps that need a person (GitHub and go/demos logins); everything else is in
the repository.

1. **Listing** — create the demo on [go/demos](http://go/demos) (submission
   guide: `company/teams/godemos/demosubmissionguide.md`). This opens the demo
   ticket and provisions the dedicated repository
   `cloud-gtm/<demo_id>-<slug>`; the link is in the listing's Click-To-Deploy
   section. GitHub access: go/use-gtm-github, then go/gtm-github-access.
2. **Template file** — download
   `Sample_Standard_Demos/org_policy/project_resource.tf` from
   [cloud-gtm/data-cloud](https://github.com/cloud-gtm/data-cloud/tree/master/Sample_Standard_Demos/org_policy)
   into `click-to-deploy/org_policy/` here, unmodified. If the template's own
   `project_config.json` has keys beyond `constraints`, keep them and set the
   constraints list to ours.
3. **Assemble the branch** — `scripts/c2d_prepare.sh <path-to-dedicated-clone>`
   copies this repository into the clone on a branch named `<ldap>-...` (the
   platform deletes branches without a username prefix) and prints what to
   check.
4. **Test by hand** in an Argolis project (below), then `git push origin
   <branch>`, note it on the demo ticket, and wait for the development-
   environment run the CDP team triggers from the branch.
5. **Pull request** into `main` with `BUG=b/<ticket>` in the description; add
   the godemos team member from the ticket as reviewer; merge.
6. **Enable** the Click-To-Deploy status in the listing's edit form and tell the
   CDP team on the ticket.

## Deploying by hand (to test the configuration)

From a machine with `gcloud` logged in as the deploying account and
Application Default Credentials for the same account
(`gcloud auth application-default login` — Terraform uses ADC; on corp
machines a plain gcloud user token is rejected when another binary presents
it), against an existing project with billing:

```bash
cd click-to-deploy/demo/terraform
terraform init
terraform apply \
  -var project_id=<project> -var project_number=<number> \
  -var gcp_account_name=<account e-mail>
```

The other five runner variables have empty defaults. Measured on the first
real run (a fresh google.com project, 2026-10-09): 41 resources, image build
38 s, Memorystore 6 m 51 s, Cloud SQL 5 m 09 s (in parallel), whole apply
≈ 12 minutes. `terraform output demo_url` prints the IAP-protected link;
`terraform output image` the image that was built. Org policies are not
touched by this root; in an Argolis project with the default
`run.allowedIngress`, relax it first (console, or apply `org_policy/`).

## Cost (list prices, us-west1)

Memorystore `SHARED_CORE_NANO` ≈ \$23/month, Cloud SQL `db-g1-small` ≈ \$25/month
plus 10 GB SSD ≈ \$2, Cloud Run with one warm instance ≈ \$5–15/month depending
on traffic, one Cloud Build of under a minute per deployment. Roughly \$55–65/month
idle.

## Teardown

`terraform destroy` removes everything, including the stores, the image
repository and the build bucket (deletion protection is off for the demo).
APIs stay enabled. Memorystore takes ≈ 10 minutes to delete. If the last step
fails with `subnetwork … is already being used by … serverless-ipv4-cloudrun-…`,
Cloud Run has not yet released the Direct VPC egress address it reserved in the
subnet; it does so on its own within minutes to an hour — run `terraform
destroy` again then.
