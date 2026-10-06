#!/usr/bin/env bash
# Build, publish and deploy the hosted demo (deploy/hosting) end to end.
#
#   scripts/deploy_hosting.sh                 build with local docker, push, terraform apply
#   scripts/deploy_hosting.sh --cloud-build   build with Cloud Build instead of local docker
#   scripts/deploy_hosting.sh --skip-build    redeploy infrastructure with this commit's image
#   scripts/deploy_hosting.sh --plan          terraform plan only: no build, no changes
#
# The image tag is the short commit hash, so Terraform always pins exactly what
# was built. Override with TAG=... (for example TAG=latest).
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${PROJECT:-ks-opm-project}
REGION=${REGION:-us-west1}
REPO=${REPO:-travel-swarm}
TAG=${TAG:-$(git rev-parse --short HEAD)}
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/app"
STATE_BUCKET="$PROJECT-tfstate"
BUILD_SA="travel-swarm-build@$PROJECT.iam.gserviceaccount.com"

if [ -z "${TF:-}" ]; then
  if command -v terraform >/dev/null 2>&1; then TF=terraform; else TF="$PWD/.tools/terraform"; fi
fi

mode=build
builder=docker
for arg in "$@"; do
  case "$arg" in
    --plan) mode=plan ;;
    --skip-build) mode=skip ;;
    --cloud-build) builder=cloud ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }

# 1. Remote state bucket (once).
say "state bucket gs://$STATE_BUCKET"
if ! gcloud storage buckets describe "gs://$STATE_BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$STATE_BUCKET" --project "$PROJECT" \
    --location "$REGION" --uniform-bucket-level-access
  gcloud storage buckets update "gs://$STATE_BUCKET" --versioning
fi

cd deploy/hosting
"$TF" init -input=false >/dev/null

if [ "$mode" = plan ]; then
  say "terraform plan (image $IMAGE:$TAG)"
  "$TF" plan -input=false -var "image_tag=$TAG"
  exit 0
fi

# 2. Registry and build identity first: the image must exist before the
#    Cloud Run service that runs it.
say "registry"
"$TF" apply -input=false -auto-approve -var "image_tag=$TAG" \
  -target=google_artifact_registry_repository.app \
  -target=google_service_account.build \
  -target=google_project_iam_member.build_writer \
  -target=google_project_iam_member.build_logs \
  -target=google_project_iam_member.build_source
cd ../..

# 3. Image.
if [ "$mode" = build ]; then
  if [ "$builder" = cloud ]; then
    say "cloud build $IMAGE:$TAG"
    gcloud builds submit --project "$PROJECT" --config cloudbuild.yaml \
      --substitutions "_IMAGE=$IMAGE,_TAG=$TAG" \
      --service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA" .
  else
    say "docker build $IMAGE:$TAG"
    # Corp machines reach PyPI only through a mirror (/etc/pip.conf); hand the
    # same index to the build. Hashes are verified regardless of the index.
    PIP_INDEX_URL=${PIP_INDEX_URL:-$(python3 -m pip config get global.index-url 2>/dev/null || true)}
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST:-$(python3 -m pip config get global.trusted-host 2>/dev/null || true)}
    build_args=()
    [ -n "$PIP_INDEX_URL" ] && build_args+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
    [ -n "$PIP_TRUSTED_HOST" ] && build_args+=(--build-arg "PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST")
    gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
    docker build --network=host ${build_args[@]+"${build_args[@]}"} --tag "$IMAGE:$TAG" --tag "$IMAGE:latest" .
    docker push "$IMAGE:$TAG"
    docker push "$IMAGE:latest"
  fi
fi

# 4. Everything else.
say "terraform apply (image $IMAGE:$TAG)"
cd deploy/hosting
"$TF" apply -input=false -auto-approve -var "image_tag=$TAG"
URL=$("$TF" output -raw demo_url)

# 5. Health through IAP. The front end answers /healthz itself (404), so the
#    check reads /api/health. IAP accepts the signed-in account's gcloud ID
#    token because the module registers gcloud's OAuth client with IAP
#    (iap_programmatic_clients); the account must be in iap_members.
say "health"
cd ../..
TOKEN=$(gcloud auth print-identity-token 2>/dev/null || true)
if [ -n "$TOKEN" ] && curl -fsS -m 30 -H "Authorization: Bearer $TOKEN" "$URL/api/health"; then
  echo
else
  echo "(could not reach $URL/api/health through IAP from here; open $URL in a browser)"
fi
say "demo: $URL"
