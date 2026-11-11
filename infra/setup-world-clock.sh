#!/usr/bin/env bash
#
# The world turn clock (WORLD_SPEC §2, §10.1): a Cloud Scheduler job that calls
# POST /internal/clock at 01:00 and 13:00 America/New_York with a Google OIDC token
# for a dedicated service account. The API verifies that token itself
# (app/games/scheduler_auth.py), because the API is public and Cloud Run's IAM
# check can't guard one route.
#
# NOT YET APPLIED. Run it when world games go live (the 2-player match was retired in code on 2026-11-10),
# after confirming with the user (it creates a service account and a scheduled
# job that resolves live worlds). The API side is configured by CLOCK_AUDIENCE and
# CLOCK_INVOKER_EMAIL in .github/workflows/api.yml; until both are set the
# endpoint answers 503 to everyone.
#
# Safe to re-run: creation is guarded and the job is updated in place.
set -uo pipefail

PROJECT="hex-general"
PROJECT_NUM="1049642396231"
REGION="us-central1"
API_URL="https://hex-api-qfzj5tg56a-uc.a.run.app"
CLOCK_SA="hex-clock@${PROJECT}.iam.gserviceaccount.com"
SCHEDULER_AGENT="service-${PROJECT_NUM}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
JOB="hex-world-clock"

exists() { eval "$1" >/dev/null 2>&1; }

gcloud services enable cloudscheduler.googleapis.com --project="$PROJECT"

exists "gcloud iam service-accounts describe $CLOCK_SA --project=$PROJECT" \
  || gcloud iam service-accounts create hex-clock --project="$PROJECT" \
       --display-name="World turn clock (Cloud Scheduler caller)"

# Cloud Scheduler's service agent mints the OIDC token for the clock SA.
gcloud iam service-accounts add-iam-policy-binding "$CLOCK_SA" --project="$PROJECT" \
  --member="serviceAccount:${SCHEDULER_AGENT}" \
  --role="roles/iam.serviceAccountTokenCreator" --quiet >/dev/null

# A late or retried call is harmless: each world's next tick moves in the same
# compare-and-set as its state, so the endpoint only resolves worlds still due.
JOB_ARGS=(
  --location="$REGION" --project="$PROJECT"
  --schedule="0 1,13 * * *" --time-zone="America/New_York"
  --uri="${API_URL}/internal/clock" --http-method=POST
  --oidc-service-account-email="$CLOCK_SA" --oidc-token-audience="$API_URL"
  --attempt-deadline=600s --max-retry-attempts=5 --min-backoff=30s --max-backoff=300s
)
if exists "gcloud scheduler jobs describe $JOB --location=$REGION --project=$PROJECT"; then
  gcloud scheduler jobs update http "$JOB" "${JOB_ARGS[@]}"
else
  gcloud scheduler jobs create http "$JOB" "${JOB_ARGS[@]}"
fi

echo "Clock job $JOB → ${API_URL}/internal/clock as $CLOCK_SA."
echo "Set CLOCK_AUDIENCE=$API_URL and CLOCK_INVOKER_EMAIL=$CLOCK_SA on the API."
