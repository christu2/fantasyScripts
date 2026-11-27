#!/usr/bin/env bash
# Deploy Serverless Multi-League Live Game-Day Poller to GCP Always Free Tier ($0.00/mo)
# ======================================================================================
# Resources provisioned in `us-central1` (all well within GCP Always Free Tier):
#   - Artifact Registry repo `fantasy-recap` (< 0.2 GB of 0.5 GB free tier)
#   - Secret Manager secrets `espn-s2`, `espn-swid` (2 of 6 free active versions)
#   - Cloud Run Job `bfl-gameday-poller` (1 vCPU / 512Mi, ~12k of 180k free vCPU-sec/mo)
#   - Cloud Scheduler Job `bfl-gameday-poller-trigger` (1 of 3 free jobs/mo,
#     scheduled ONLY on Thu/Fri/Sat/Sun/Mon every 5 min!)
#
# Usage:
#   ./cloud_run/deploy_cloudrun.sh
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT_ID="${GCP_PROJECT:-bfl-fantasy}"
REGION="${GCP_REGION:-us-central1}"
REPO="fantasy-recap"
JOB_NAME="bfl-gameday-poller"
SCHEDULER_NAME="bfl-gameday-poller-trigger"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${JOB_NAME}:latest"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECAP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="$(cd "${RECAP_DIR}/.." && pwd)/.env"

echo "🚀 Deploying ${JOB_NAME} to GCP Project ${PROJECT_ID} (${REGION})..."

# 1. Enable required serverless APIs
echo "▶ [1/7] Ensuring serverless APIs are enabled..."
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# 2. Ensure Artifact Registry repo exists
echo "▶ [2/7] Checking Artifact Registry repo ${REPO}..."
if ! gcloud artifacts repositories describe "${REPO}" --location="${REGION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Serverless FantasyRecap Game-Day Poller" \
    --project="${PROJECT_ID}" --quiet
fi

# 3. Sync ESPN_S2 and ESPN_SWID into Secret Manager (2 versions total, inside 6-version free tier)
echo "▶ [3/7] Syncing ESPN credentials into Secret Manager..."
if [[ -z "${ESPN_S2:-}" || -z "${ESPN_SWID:-}" ]] && [[ -f "${ENV_FILE}" ]]; then
  ESPN_S2="${ESPN_S2:-$(python3 -c 'from dotenv import dotenv_values; print(dotenv_values("'"${ENV_FILE}"'").get("ESPN_S2", ""))')}"
  ESPN_SWID="${ESPN_SWID:-$(python3 -c 'from dotenv import dotenv_values; print(dotenv_values("'"${ENV_FILE}"'").get("ESPN_SWID") or dotenv_values("'"${ENV_FILE}"'").get("SWID", ""))')}"
fi

if [[ -z "${ESPN_S2:-}" || -z "${ESPN_SWID:-}" ]]; then
  echo "❌ ESPN_S2 and ESPN_SWID must be set in environment or ${ENV_FILE}"
  exit 1
fi

upsert_secret() {
  local name="$1"
  local val="$2"
  if ! gcloud secrets describe "${name}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    gcloud secrets create "${name}" --replication-policy="automatic" --project="${PROJECT_ID}" --quiet
    printf '%s' "${val}" | gcloud secrets versions add "${name}" --data-file=- --project="${PROJECT_ID}" --quiet >/dev/null
    return
  fi
  local existing
  existing="$(gcloud secrets versions access latest --secret="${name}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true)"
  if [[ "${existing}" != "${val}" ]]; then
    printf '%s' "${val}" | gcloud secrets versions add "${name}" --data-file=- --project="${PROJECT_ID}" --quiet >/dev/null
  fi
}

upsert_secret "espn-s2" "${ESPN_S2}"
upsert_secret "espn-swid" "${ESPN_SWID}"

# 4. Grant default Compute Engine SA minimal access to Secret Manager + GCS Hub Bucket + Run Invoker
echo "▶ [4/7] Configuring IAM permissions for service account..."
PROJECT_NUM="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)' --quiet)"
SA_EMAIL="${PROJECT_NUM}-compute@developer.gserviceaccount.com"

for sec in espn-s2 espn-swid; do
  gcloud secrets add-iam-policy-binding "${sec}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}" --quiet >/dev/null
done

gcloud storage buckets add-iam-policy-binding "gs://bfl-hub-7x9k2" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  --project="${PROJECT_ID}" --quiet >/dev/null

# 5. Build lean container image via Cloud Build (120 free build-min/day)
echo "▶ [5/7] Building container image via Cloud Build..."
TMP_BUILD="$(mktemp -d)"
trap 'rm -rf "${TMP_BUILD}"' EXIT
rsync -a \
  --exclude='live_state/' \
  --exclude='output/' \
  --exclude='tests/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  "${RECAP_DIR}/" "${TMP_BUILD}/"
cp "${RECAP_DIR}/cloud_run/Dockerfile" "${TMP_BUILD}/Dockerfile"

gcloud builds submit "${TMP_BUILD}" \
  --tag="${IMAGE}" \
  --project="${PROJECT_ID}" --quiet

# 6. Create or update Cloud Run Job (1 vCPU, 512Mi, max 1 retry, 5m timeout)
echo "▶ [6/7] Deploying Cloud Run Job ${JOB_NAME}..."
gcloud run jobs deploy "${JOB_NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --service-account="${SA_EMAIL}" \
  --cpu=1 \
  --memory=512Mi \
  --max-retries=1 \
  --task-timeout=300s \
  --set-env-vars="ESPN_LEAGUE_ID=157057" \
  --set-secrets="ESPN_S2=espn-s2:latest,ESPN_SWID=espn-swid:latest" --quiet

# Grant SA permission to invoke the Cloud Run Job from Cloud Scheduler
gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" --quiet >/dev/null

# 7. Create or update Cloud Scheduler trigger (every 5 min on NFL days Thu/Fri/Sat/Sun/Mon in CT)
echo "▶ [7/7] Configuring Cloud Scheduler trigger ${SCHEDULER_NAME}..."
SCHED_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
CRON_EXPR="*/5 11-23 * * 0,1,4,5,6"

if gcloud scheduler jobs describe "${SCHEDULER_NAME}" --location="${REGION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_NAME}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --schedule="${CRON_EXPR}" \
    --time-zone="America/Chicago" \
    --uri="${SCHED_URI}" \
    --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}" --quiet
else
  gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --schedule="${CRON_EXPR}" \
    --time-zone="America/Chicago" \
    --uri="${SCHED_URI}" \
    --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}" --quiet
fi

echo "✅ Serverless Cloud Run Job & Cloud Scheduler deployed!"
echo "   Manual test: gcloud run jobs execute ${JOB_NAME} --region=${REGION} --project=${PROJECT_ID} --wait"
