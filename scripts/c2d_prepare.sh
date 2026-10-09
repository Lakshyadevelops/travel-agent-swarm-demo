#!/usr/bin/env bash
# Assemble the go/demos click-to-deploy branch in the dedicated cloud-gtm
# repository from this checkout.
#
#   scripts/c2d_prepare.sh <path-to-clone-of-cloud-gtm/<demo_id>-<slug>> [branch]
#
# The dedicated repository must hold the whole application (the image is built
# from its root), so this copies the repository -- minus local state -- into the
# clone on a fresh branch off origin/main, then checks the three things the
# go/demos reviewers look at first. Nothing is committed or pushed; review the
# diff, commit, push, and open the pull request as click-to-deploy/README.md
# describes.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:?path to the dedicated repository clone}"
branch="${2:-${USER}-travel-swarm-click-to-deploy}"

[ -d "$dest/.git" ] || { echo "not a git clone: $dest" >&2; exit 1; }
case "$branch" in
  "${USER}"-*) ;;
  *) echo "branch must start with your LDAP ($USER-...): go/demos deletes other branches" >&2; exit 1 ;;
esac

say() { printf '\n== %s\n' "$*"; }

say "branch $branch off origin/main in $dest"
git -C "$dest" fetch origin
git -C "$dest" checkout -q -B "$branch" origin/main

say "copying the repository (overlay; files the dedicated repo already has are kept unless we ship the same path)"
rsync -a \
  --exclude .git --exclude .venv --exclude .tools --exclude .env \
  --exclude runs --exclude .pytest_cache --exclude '__pycache__' \
  --exclude '.terraform' --exclude '*.tfstate*' --exclude '.terraform.tfstate*' \
  --exclude docker/data \
  "$here"/ "$dest"/

say "checks"
status=0
tf_root="$dest/click-to-deploy/demo/terraform"
if [ -f "$dest/click-to-deploy/org_policy/project_resource.tf" ]; then
  echo "ok   org_policy/project_resource.tf present (copied from the Sample_Standard_Demos template)"
else
  echo "MISSING click-to-deploy/org_policy/project_resource.tf -- copy it verbatim from"
  echo "        https://github.com/cloud-gtm/data-cloud/tree/master/Sample_Standard_Demos/org_policy"
  status=1
fi
for v in project_id project_name project_number org_id gcp_account_name \
         deployment_service_account_name data_location secret_stored_project; do
  grep -q "variable \"$v\"" "$tf_root/base_variables.tf" \
    && echo "ok   variable $v" || { echo "MISSING variable $v"; status=1; }
done
if grep -q '"restore_post_deployment"' "$dest/click-to-deploy/org_policy/project_config.json"; then
  echo "ok   project_config.json uses restore_post_deployment"
else
  echo "BAD  project_config.json lacks restore_post_deployment"; status=1
fi
if grep -rq -- '--require-hashes' "$dest/Dockerfile"; then
  echo "ok   Dockerfile installs with --require-hashes"
else
  echo "BAD  Dockerfile does not use --require-hashes"; status=1
fi
if [ -f "$dest/.env" ]; then echo "BAD  .env copied"; status=1; else echo "ok   no .env in the clone"; fi
if command -v terraform >/dev/null 2>&1; then
  (cd "$tf_root" && terraform fmt -check -recursive >/dev/null \
    && echo "ok   terraform fmt" || { echo "BAD  terraform fmt -check"; status=1; })
fi

say "next"
cat <<EOF
  cd $dest && git status
  git add -A && git commit -m "Travel Concierge Swarm: click-to-deploy"
  git push origin $branch
  # note the branch on the demo ticket; after the dev-environment run passes,
  # open the PR into main with BUG=b/<ticket> and the ticket's godemos reviewer.
EOF
exit $status
