#!/usr/bin/env bash
# Build the API image and deploy it to Cloud Run (TRD section 11).
# Usage: backend/deploy/deploy.sh <gcp-project-id> [region] [image-tag]
# Needs deploy/env.production.yaml (copy of env.production.yaml.example) and a completed setup.sh.
set -euo pipefail

PROJECT="${1:?usage: deploy.sh <gcp-project-id> [region] [image-tag]}"
REGION="${2:-asia-south1}"
TAG="${3:-$(date +%Y%m%d-%H%M%S)}"
SERVICE="gitresolve-api"
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(dirname "$HERE")"
ENV_FILE="$HERE/env.production.yaml"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/gitresolve/api:$TAG"
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE (copy env.production.yaml.example)"; exit 1; }

NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
URL="https://$SERVICE-$NUMBER.$REGION.run.app"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
sed "s|\"AUTO\"|\"$URL/api/v1/auth/github/callback\"|" "$ENV_FILE" > "$RENDERED"

echo "== Building $IMAGE"
gcloud builds submit "$BACKEND" --project "$PROJECT" --tag "$IMAGE" --quiet

echo "== Deploying $SERVICE"
gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE" --quiet \
  --service-account "gitresolve-api@$PROJECT.iam.gserviceaccount.com" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 --memory 512Mi --cpu 1 --concurrency 40 --timeout 60 --port 8080 \
  --env-vars-file "$RENDERED" \
  --set-secrets "SESSION_JWT_SECRET=session-jwt-secret:latest,GITHUB_APP_CLIENT_SECRET=github-app-client-secret:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest"

LIVE="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo "== Service URL: $LIVE"
[ "$LIVE" = "$URL" ] || echo "WARNING: URL differs from the callback host used ($URL). Set GITHUB_APP_CALLBACK_URL explicitly and redeploy."
echo "== Health check"
curl -fsS "$LIVE/health" && echo
echo "GitHub App callback URL to register: $LIVE/api/v1/auth/github/callback"
echo "Set NEXT_PUBLIC_API_BASE_URL=$LIVE in Vercel."
