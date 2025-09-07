#!/usr/bin/env bash
# gcp-setup.sh — GitHub Actions → GCP Workload Identity setup

set -euo pipefail

# ---------- Defaults (edit if you wish) ----------
PROJECT_ID_DEFAULT="dulcet-iterator-471410-m1"
REGION_DEFAULT="australia-southeast1"
BUCKET_NAME_DEFAULT="pdf-agent-x"              # bare name (no gs://)
GH_OWNER_DEFAULT="inderanz"
GH_REPO_DEFAULT="ai-requirement-agent"         # no .git suffix
POOL_ID_DEFAULT="github-actions-pool-2"        # use the already created pool
PROVIDER_ID_DEFAULT="github-provider"          # use the already created provider
SA_NAME_DEFAULT="github-actions-sa"
# ------------------------------------------------

NONINTERACTIVE="N"
if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then NONINTERACTIVE="Y"; fi

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "❌ '$1' not found"; exit 1; }
}
need gcloud

prompt_val () {
  local prompt="$1"; shift
  local def="$1"; shift
  if [ "$NONINTERACTIVE" = "Y" ]; then
    echo "$def"; return
  fi
  local var
  read -r -p "${prompt} [${def}]: " var || true
  echo "${var:-$def}"
}

yes_no () {
  local prompt="$1"; local def="${2:-N}"
  if [ "$NONINTERACTIVE" = "Y" ]; then echo "$def"; return; fi
  local ans
  read -r -p "${prompt} [${def}/$( [ "$def" = "Y" ] && echo N || echo Y )]: " ans || true
  ans="${ans:-$def}"
  case "$ans" in y|Y|yes|YES) echo "Y" ;; *) echo "N" ;; esac
}

normalise_repo () {
  # drop trailing .git if pasted accidentally
  echo "$1" | sed -E 's/\.git$//'
}

validate_ref () {
  local ref="$1"
  case "$ref" in
    refs/*) echo "$ref" ;;
    *) echo "refs/heads/main" ;;  # safe fallback
  esac
}

echo
echo "== GitHub Actions → GCP Workload Identity one-time setup =="

PROJECT_ID="$(prompt_val "GCP Project ID" "$PROJECT_ID_DEFAULT")"
REGION="$(prompt_val "Preferred region for Vertex AI" "$REGION_DEFAULT")"
BUCKET_NAME="$(prompt_val "GCS bucket (bare name, no gs://)" "$BUCKET_NAME_DEFAULT")"
GH_OWNER="$(prompt_val "GitHub owner/org" "$GH_OWNER_DEFAULT")"
GH_REPO_RAW="$(prompt_val "GitHub repo name (no .git)" "$GH_REPO_DEFAULT")"
GH_REPO="$(normalise_repo "$GH_REPO_RAW")"
POOL_ID="$(prompt_val "Workload Identity Pool ID" "$POOL_ID_DEFAULT")"
PROVIDER_ID="$(prompt_val "Workload Identity Provider ID" "$PROVIDER_ID_DEFAULT")"
SA_NAME="$(prompt_val "Service Account name (prefix only)" "$SA_NAME_DEFAULT")"

echo
echo "Scope of trust for GitHub OIDC:"
echo "  1) Only this repo (${GH_OWNER}/${GH_REPO})  [recommended]"
echo "  2) All repos in owner/org (${GH_OWNER})"
if [ "$NONINTERACTIVE" = "Y" ]; then
  SCOPE="1"
else
  read -r -p "Choose 1 or 2: " SCOPE; SCOPE="${SCOPE:-1}"
fi

LOCK_REF="$(yes_no 'Do you want to lock to a specific ref (e.g. refs/heads/main)?' 'N')"
GH_REF_FILTER=""
if [ "$LOCK_REF" = "Y" ]; then
  GH_REF_FILTER="$(prompt_val "Git ref to lock (e.g. refs/heads/main)" "refs/heads/main")"
  GH_REF_FILTER="$(validate_ref "$GH_REF_FILTER")"
fi

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo
echo "== Applying config to project ${PROJECT_ID} =="
gcloud config set project "${PROJECT_ID}"

# Try to align local ADC quota project (optional; ignore failures)
if gcloud auth application-default print-access-token >/dev/null 2>&1; then
  gcloud auth application-default set-quota-project "${PROJECT_ID}" >/dev/null 2>&1 || true
fi

echo
echo "Enabling required APIs…"
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com

echo
echo "Creating service account (if not exists)…"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="GitHub Actions SA for PDF Agent"
else
  echo "Service account ${SA_EMAIL} already exists."
fi

echo
echo "Using existing Workload Identity Pool: ${POOL_ID}"
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" --location=global >/dev/null 2>&1; then
  echo "❌ Pool ${POOL_ID} not found. Please create it manually or check your project."
  exit 1
else
  echo "Pool ${POOL_ID} exists."
fi

echo
echo "Using existing Workload Identity Provider: ${PROVIDER_ID}"
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
      --workload-identity-pool="${POOL_ID}" --location=global >/dev/null 2>&1; then
  echo "❌ Provider ${PROVIDER_ID} not found. Please create it manually or check your project."
  exit 1
else
  echo "Provider ${PROVIDER_ID} exists."
fi

echo
echo "Fetching project number…"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo
echo "Binding Workload Identity user → service account…"
if [ "${SCOPE}" = "1" ]; then
  MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GH_OWNER}/${GH_REPO}"
elif [ "${SCOPE}" = "2" ]; then
  MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository_owner/${GH_OWNER}"
else
  echo "❌ Invalid choice for scope (must be 1 or 2)"; exit 1
fi

# Base binding (no condition)
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${MEMBER}" || true

# Optional ref lock: add a **second binding** with a condition
if [ -n "${GH_REF_FILTER}" ]; then
  echo "Adding conditional ref lock to ${GH_REF_FILTER} …"
  gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/*" \
    --condition="expression=attribute.repository=='${GH_OWNER}/${GH_REPO}' && attribute.ref=='${GH_REF_FILTER}',title=repo_and_ref_lock,description=Limit_to_specific_ref" || true
fi

echo
echo "Creating GCS bucket (if not exists)…"
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
else
  echo "Bucket gs://${BUCKET_NAME} already exists."
fi

echo
echo "Granting Storage objectAdmin on the bucket to the SA…"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" || true

echo
echo "Granting Vertex AI user to the SA…"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user" || true

echo
echo "== ✅ Done =="
PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

echo "Paste these into GitHub → Settings → Secrets and variables → Actions:"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER = ${PROVIDER_RESOURCE}"
echo "  GCP_SERVICE_ACCOUNT_EMAIL      = ${SA_EMAIL}"
echo
echo "Use this bucket in your workflow inputs: gs://${BUCKET_NAME}"
