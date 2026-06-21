#!/usr/bin/env bash
#
# One-time GCP setup for the hex-general Cloud Run deploy pipeline.
#
# Creates: Artifact Registry repo, a least-privilege GitHub Actions deployer SA,
# a dedicated Cloud Run runtime SA, the Supabase service-role Secret Manager
# container, and Workload Identity Federation locked to the GitHub repo (keyless
# CI -> GCP auth; no service-account JSON keys).
#
# Safe to re-run: resource creation is guarded and IAM bindings are idempotent.
# Requires an identity with Owner/admin on the project (local gcloud impersonates
# llm-agent-owner; see memory hex-general-gcp-keyless-access).
set -uo pipefail

PROJECT="hex-general"
PROJECT_NUM="1049642396231"
REGION="us-central1"
AR_REPO="hex"
GITHUB_REPO="tcgarvin/hex-general"
POOL="github"
PROVIDER="github"

DEPLOYER="github-deployer@${PROJECT}.iam.gserviceaccount.com"
RUNTIME="hex-api-runtime@${PROJECT}.iam.gserviceaccount.com"

exists() { eval "$1" >/dev/null 2>&1; }

# --- Artifact Registry ---
exists "gcloud artifacts repositories describe $AR_REPO --location=$REGION --project=$PROJECT" \
  || gcloud artifacts repositories create "$AR_REPO" --repository-format=docker \
       --location="$REGION" --project="$PROJECT" --description="Hex API container images"

# --- Service accounts ---
exists "gcloud iam service-accounts describe $DEPLOYER --project=$PROJECT" \
  || gcloud iam service-accounts create github-deployer --project="$PROJECT" \
       --display-name="GitHub Actions deployer"
exists "gcloud iam service-accounts describe $RUNTIME --project=$PROJECT" \
  || gcloud iam service-accounts create hex-api-runtime --project="$PROJECT" \
       --display-name="Cloud Run runtime for hex-api"

# --- Deployer project roles (least privilege) ---
for ROLE in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${DEPLOYER}" --role="$ROLE" --condition=None --quiet >/dev/null
done

# Deployer must be able to deploy a service that runs AS the runtime SA.
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --project="$PROJECT" \
  --member="serviceAccount:${DEPLOYER}" --role="roles/iam.serviceAccountUser" --quiet >/dev/null

# --- Secret Manager: container for the Supabase service-role key (value added manually) ---
exists "gcloud secrets describe supabase-service-role-key --project=$PROJECT" \
  || gcloud secrets create supabase-service-role-key --replication-policy=automatic --project="$PROJECT"
gcloud secrets add-iam-policy-binding supabase-service-role-key --project="$PROJECT" \
  --member="serviceAccount:${RUNTIME}" --role="roles/secretmanager.secretAccessor" --quiet >/dev/null

# --- Workload Identity Federation (GitHub OIDC -> deployer SA) ---
exists "gcloud iam workload-identity-pools describe $POOL --location=global --project=$PROJECT" \
  || gcloud iam workload-identity-pools create "$POOL" --location=global --project="$PROJECT" \
       --display-name="GitHub Actions"
exists "gcloud iam workload-identity-pools providers describe $PROVIDER --location=global --workload-identity-pool=$POOL --project=$PROJECT" \
  || gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
       --workload-identity-pool="$POOL" --project="$PROJECT" --display-name="GitHub" \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
       --attribute-condition="assertion.repository=='${GITHUB_REPO}'"

# Only this repo may impersonate the deployer SA.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --project="$PROJECT" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" \
  --quiet >/dev/null

echo
echo "=== Add these as GitHub repository VARIABLES (not secrets) ==="
echo "GCP_WIF_PROVIDER=projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "GCP_DEPLOY_SA=${DEPLOYER}"
