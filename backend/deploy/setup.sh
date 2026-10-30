#!/usr/bin/env bash
# One-time Google Cloud setup for GitResolve (idempotent; safe to re-run).
# Usage: backend/deploy/setup.sh <gcp-project-id> [region]      default region: asia-south1
# Needs: gcloud (logged in, billing enabled on the project), openssl.
set -euo pipefail

PROJECT="${1:?usage: setup.sh <gcp-project-id> [region]}"
REGION="${2:-asia-south1}"
API_SA="gitresolve-api"
WORKER_SA="gitresolve-worker"
API_SECRETS=(session-jwt-secret github-app-client-secret github-app-private-key)
WORKER_SECRETS=(github-app-private-key gemini-api-key)
ALL_SECRETS=(session-jwt-secret github-app-client-secret github-app-private-key gemini-api-key)
G=(gcloud --project "$PROJECT" --quiet)

echo "== Enabling APIs"
"${G[@]}" services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com iam.googleapis.com logging.googleapis.com

echo "== Artifact Registry repository"
"${G[@]}" artifacts repositories create gitresolve --repository-format=docker --location="$REGION" \
  --description="GitResolve images" 2>/dev/null || echo "   exists"

echo "== Service accounts (least privilege, no keys)"
for sa in "$API_SA" "$WORKER_SA"; do
  "${G[@]}" iam service-accounts create "$sa" --display-name="$sa" 2>/dev/null || echo "   $sa exists"
  for role in roles/datastore.user roles/logging.logWriter; do
    "${G[@]}" projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:$sa@$PROJECT.iam.gserviceaccount.com" --role="$role" --condition=None >/dev/null
  done
done

echo "== Firestore (Native mode, $REGION)"
"${G[@]}" firestore databases create --location="$REGION" --type=firestore-native 2>/dev/null || echo "   exists"
"${G[@]}" firestore indexes composite create --collection-group=tasks --query-scope=collection \
  --field-config=field-path=userId,order=ascending \
  --field-config=field-path=createdAt,order=descending 2>/dev/null || echo "   index exists"
for group in tasks events attempts artifacts; do
  "${G[@]}" firestore fields ttls update expiresAt --collection-group="$group" --enable-ttl >/dev/null 2>&1 \
    || echo "   TTL for $group exists or pending"
done

echo "== Secret Manager"
for name in "${ALL_SECRETS[@]}"; do
  "${G[@]}" secrets create "$name" --replication-policy=automatic 2>/dev/null || true
done
has_version() { [ -n "$("${G[@]}" secrets versions list "$1" --limit=1 --format='value(name)' 2>/dev/null)" ]; }

if ! has_version session-jwt-secret; then
  openssl rand -base64 48 | tr -d '\n' | "${G[@]}" secrets versions add session-jwt-secret --data-file=- >/dev/null
  echo "   generated session-jwt-secret"
fi
if ! has_version github-app-client-secret; then
  read -rsp "GitHub App client secret (Enter to skip): " value; echo
  [ -n "$value" ] && printf '%s' "$value" | "${G[@]}" secrets versions add github-app-client-secret --data-file=- >/dev/null
fi
if ! has_version github-app-private-key; then
  read -rp "Path to GitHub App private key .pem (Enter to skip): " pem
  [ -n "$pem" ] && "${G[@]}" secrets versions add github-app-private-key --data-file="$pem" >/dev/null
fi
if ! has_version gemini-api-key; then
  read -rsp "Gemini API key for the worker (Enter to skip): " value; echo
  [ -n "$value" ] && printf '%s' "$value" | "${G[@]}" secrets versions add gemini-api-key --data-file=- >/dev/null
fi

echo "== Secret access (per secret, per service account)"
for name in "${API_SECRETS[@]}"; do
  "${G[@]}" secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:$API_SA@$PROJECT.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor >/dev/null
done
for name in "${WORKER_SECRETS[@]}"; do
  "${G[@]}" secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:$WORKER_SA@$PROJECT.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor >/dev/null
done

echo
echo "Done. Next: copy deploy/env.production.yaml.example to deploy/env.production.yaml, edit it, then run deploy/deploy.sh."
echo "Also deny client access to Firestore: cd backend/deploy && firebase deploy --only firestore --project $PROJECT"
