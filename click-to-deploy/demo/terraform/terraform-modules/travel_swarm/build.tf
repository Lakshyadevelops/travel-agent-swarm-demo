# Optional in-project image build.
#
# When var.image is empty the module builds the demo image itself: it creates an
# Artifact Registry repository in the target project and runs the repository's
# cloudbuild.yaml there with `gcloud builds submit`, from var.source_dir (the
# checkout that contains the Dockerfile). This is what go/demos click-to-deploy
# needs -- each click lands in a brand-new Argolis project that cannot pull from
# anyone else's registry -- and it works wherever terraform runs next to gcloud
# (the click-to-deploy runner image has both; so does a Cloudtop).
#
# The tag is a hash of the files that go into the image, so a source change
# rolls a new Cloud Run revision and an unchanged tree is not rebuilt.

locals {
  build_image = var.image == ""
  source_dir  = var.source_dir != "" ? var.source_dir : path.module

  # What the Dockerfile COPYs, plus the Dockerfile itself.
  build_files = local.build_image ? sort(concat(
    [for f in fileset(local.source_dir, "app/**") : f if !can(regex("(^|/)__pycache__/|\\.pyc$", f))],
    ["Dockerfile", "requirements-runtime.txt", "results/llm_trace.jsonl"],
  )) : []
  build_tag = local.build_image ? substr(sha1(join("\n", [
    for f in local.build_files : "${f} ${filesha1("${local.source_dir}/${f}")}"
  ])), 0, 12) : ""

  build_repository = "${var.region}-docker.pkg.dev/${var.project_id}/${var.name_prefix}/app"
  image            = local.build_image ? "${local.build_repository}:${local.build_tag}" : var.image
}

resource "google_artifact_registry_repository" "app" {
  count = local.build_image ? 1 : 0

  project       = var.project_id
  location      = var.region
  repository_id = var.name_prefix
  format        = "DOCKER"
  description   = "Travel Concierge Swarm demo image, built in this project"
  labels        = var.labels

  depends_on = [google_project_service.apis]
}

# Cloud Build runs as its own account with just what a build needs. New
# projects (and Argolis in particular) do not hand the default compute account
# any roles, so relying on it would fail.
resource "google_service_account" "build" {
  count = local.build_image ? 1 : 0

  project      = var.project_id
  account_id   = "${var.name_prefix}-build"
  display_name = "Travel Concierge Swarm (image build)"

  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository_iam_member" "build_writer" {
  count = local.build_image ? 1 : 0

  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.app[0].name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.build[0].member
}

resource "google_project_iam_member" "build_logs" {
  count = local.build_image ? 1 : 0

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.build[0].member
}

# Where `gcloud builds submit` stages the source tarball.
resource "google_storage_bucket" "build_source" {
  count = local.build_image ? 1 : 0

  project                     = var.project_id
  name                        = "${var.project_id}-${var.name_prefix}-build"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  labels                      = var.labels

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "build_source_reader" {
  count = local.build_image ? 1 : 0

  bucket = google_storage_bucket.build_source[0].name
  role   = "roles/storage.objectViewer"
  member = google_service_account.build[0].member
}

resource "null_resource" "build" {
  count = local.build_image ? 1 : 0

  triggers = {
    image = local.image
  }

  lifecycle {
    precondition {
      condition     = fileexists("${local.source_dir}/Dockerfile") && fileexists("${local.source_dir}/cloudbuild.yaml")
      error_message = "source_dir (${local.source_dir}) must be the repository root: Dockerfile and cloudbuild.yaml were not found there."
    }
  }

  provisioner "local-exec" {
    working_dir = local.source_dir
    command     = <<-EOT
      gcloud builds submit . \
        --project "${var.project_id}" \
        --region "${var.region}" \
        --config cloudbuild.yaml \
        --service-account "${google_service_account.build[0].id}" \
        --gcs-source-staging-dir "gs://${google_storage_bucket.build_source[0].name}/source" \
        --substitutions "_IMAGE=${local.build_repository},_TAG=${local.build_tag}" \
        --quiet
    EOT
  }

  depends_on = [
    google_artifact_registry_repository_iam_member.build_writer,
    google_project_iam_member.build_logs,
    google_storage_bucket_iam_member.build_source_reader,
  ]
}
