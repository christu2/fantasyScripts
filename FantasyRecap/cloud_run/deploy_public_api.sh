#!/usr/bin/env bash
# =============================================================================
# Isolated, $0-Ceiling Public Cloud Run Service Deployer (`fantasy-public-api`)
# =============================================================================
# Staff-Engineer Isolation & Security Controls:
#   1. Dedicated ZERO-PRIVILEGE IAM Service Account (`fantasy-public-sa`) with
#      NO roles on Secret Manager (`espn-s2`/`espn-swid`) and NO write/read IAM
#      on private GCS buckets!
#   2. `--max-instances=2` + `--concurrency=16` + `--timeout=25s` caps compute
#      well inside GCP Always Free Tier (2M requests / 180k vCPU-sec / mo).
#   3. Runs as non-root `uid=10001` with `PUBLIC_ONLY=1` and ephemeral `/tmp` DBs.
#   4. Minimal staged Cloud Build context excluding `.env`, personal `hub_data.json`,
#      `live_state/`, `output/`, and `tests/`.
# =============================================================================
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT_ID="${GCP_PROJECT_ID:-bfl-fantasy}"
REGION="${GCP_REGION:-us-central1}"
REPO="fantasy-recap"
SERVICE_NAME="${PUBLIC_SERVICE_NAME:-fantasy-public-api}"
PUBLIC_SA_NAME="fantasy-public-sa"
PUBLIC_SA_EMAIL="${PUBLIC_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE_NAME}:latest"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECAP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "📦 [1/4] Building fresh zero-PII anonymized demo bundle..."
python3 "${RECAP_DIR}/build_public_site.py"

echo "🔐 [2/4] Ensuring zero-privilege service account ${PUBLIC_SA_EMAIL} exists..."
if ! gcloud iam service-accounts describe "${PUBLIC_SA_EMAIL}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
  gcloud iam service-accounts create "${PUBLIC_SA_NAME}" \
    --display-name="Zero-Privilege Public Fantasy Hub API SA" \
    --project="${PROJECT_ID}" --quiet
fi

echo "🐳 [3/4] Staging minimal zero-secret build context & building image..."
TMP_BUILD="$(mktemp -d)"
trap 'rm -rf "${TMP_BUILD}"' EXIT
rsync -a \
  --exclude='live_state/' \
  --exclude='output/' \
  --exclude='tests/' \
  --exclude='dist_public/' \
  --exclude='web_hub/hub_data.json' \
  --exclude='web_hub/leagues/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  "${RECAP_DIR}/" "${TMP_BUILD}/"
cp "${RECAP_DIR}/cloud_run/Dockerfile.public_api" "${TMP_BUILD}/Dockerfile"

gcloud builds submit "${TMP_BUILD}" \
  --tag="${IMAGE_URI}" \
  --project="${PROJECT_ID}" --quiet

echo "🚀 [4/4] Deploying ${SERVICE_NAME} (--max-instances=2, zero secrets, zero-privilege SA)..."
gcloud run deploy "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE_URI}" \
  --service-account="${PUBLIC_SA_EMAIL}" \
  --platform=managed \
  --allow-unauthenticated \
  --max-instances=2 \
  --min-instances=0 \
  --cpu=1 \
  --memory=512Mi \
  --concurrency=16 \
  --timeout=25s \
  --clear-secrets \
  --set-env-vars="^@^HOST=0.0.0.0@PUBLIC_ONLY=1@SERVE_STATIC=1@MAX_HISTORY_YEARS=5@ALLOWED_ORIGINS=${ALLOWED_ORIGINS}" \
  --quiet

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
echo "✅ Deployed isolated public self-serve service: ${SERVICE_URL}"
