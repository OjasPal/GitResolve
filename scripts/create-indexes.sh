#!/usr/bin/env bash
# Creates the Firestore composite index and TTL policies (Backend Schema, sections 8-9).
# Usage: scripts/create-indexes.sh <gcp-project-id>
set -euo pipefail
PROJECT="${1:?usage: create-indexes.sh <gcp-project-id>}"
DB="${FIRESTORE_DATABASE:-(default)}"

# tasks(userId ASC, createdAt DESC): paged task list and daily quota count.
gcloud firestore indexes composite create \
  --project="$PROJECT" --database="$DB" \
  --collection-group=tasks --query-scope=collection \
  --field-config=field-path=userId,order=ascending \
  --field-config=field-path=createdAt,order=descending || echo "index may already exist"

# TTL on expiresAt for each collection group (asynchronous; requires billing).
for group in tasks events attempts artifacts; do
  gcloud firestore fields ttls update expiresAt \
    --project="$PROJECT" --database="$DB" \
    --collection-group="$group" --enable-ttl || echo "TTL for $group may already be enabled"
done
