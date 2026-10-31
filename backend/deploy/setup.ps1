# One-time Google Cloud setup for GitResolve (idempotent; safe to re-run).
# Usage (PowerShell): .\backend\deploy\setup.ps1 -Project <gcp-project-id> [-Region asia-south1]
# Needs: gcloud (logged in, billing enabled on the project).
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string]$Region = "asia-south1"
)
$ErrorActionPreference = "Stop"
$ApiSa = "gitresolve-api"
$WorkerSa = "gitresolve-worker"
$ApiSecrets = @("session-jwt-secret", "github-app-client-secret", "github-app-private-key")
$WorkerSecrets = @("github-app-private-key", "gemini-api-key")
$AllSecrets = @("session-jwt-secret", "github-app-client-secret", "github-app-private-key", "gemini-api-key")

function G {
  # Runs gcloud and stops on failure.
  & gcloud --project $Project --quiet @args
  if ($LASTEXITCODE -ne 0) { throw "gcloud $($args -join ' ') failed" }
}
function GOptional {
  # Runs gcloud, hides output and ignores "already exists" style failures.
  & gcloud --project $Project --quiet @args 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "   (skipped: already exists or pending)" }
  $global:LASTEXITCODE = 0
}
function HasVersion([string]$Name) {
  $out = & gcloud --project $Project --quiet secrets versions list $Name --limit=1 "--format=value(name)" 2>$null
  return -not [string]::IsNullOrWhiteSpace(($out | Out-String))
}
function AddSecretFromText([string]$Name, [string]$Text) {
  $tmp = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
    G secrets versions add $Name "--data-file=$tmp" | Out-Null
  } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}
function ReadSecretText([string]$Prompt) {
  $secure = Read-Host $Prompt -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

Write-Host "== Enabling APIs"
G services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com `
  secretmanager.googleapis.com firestore.googleapis.com iam.googleapis.com logging.googleapis.com

Write-Host "== Artifact Registry repository"
GOptional artifacts repositories create gitresolve --repository-format=docker "--location=$Region" "--description=GitResolve images"

Write-Host "== Service accounts (least privilege, no keys)"
foreach ($sa in @($ApiSa, $WorkerSa)) {
  GOptional iam service-accounts create $sa "--display-name=$sa"
  foreach ($role in @("roles/datastore.user", "roles/logging.logWriter")) {
    G projects add-iam-policy-binding $Project "--member=serviceAccount:$sa@$Project.iam.gserviceaccount.com" "--role=$role" --condition=None | Out-Null
  }
}

Write-Host "== Firestore (Native mode, $Region)"
GOptional firestore databases create "--location=$Region" --type=firestore-native
GOptional firestore indexes composite create --collection-group=tasks --query-scope=collection `
  --field-config=field-path=userId,order=ascending --field-config=field-path=createdAt,order=descending
foreach ($group in @("tasks", "events", "attempts", "artifacts")) {
  GOptional firestore fields ttls update expiresAt "--collection-group=$group" --enable-ttl
}

Write-Host "== Secret Manager"
foreach ($name in $AllSecrets) { GOptional secrets create $name --replication-policy=automatic }

if (-not (HasVersion "session-jwt-secret")) {
  $bytes = New-Object byte[] 48
  $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
  $rng.GetBytes($bytes)
  AddSecretFromText "session-jwt-secret" ([Convert]::ToBase64String($bytes))
  Write-Host "   generated session-jwt-secret"
}
if (-not (HasVersion "github-app-client-secret")) {
  $value = ReadSecretText "GitHub App client secret (Enter to skip)"
  if ($value) { AddSecretFromText "github-app-client-secret" $value }
}
if (-not (HasVersion "github-app-private-key")) {
  $pem = Read-Host "Path to GitHub App private key .pem (Enter to skip)"
  if ($pem) { G secrets versions add github-app-private-key "--data-file=$pem" | Out-Null }
}
if (-not (HasVersion "gemini-api-key")) {
  $value = ReadSecretText "Gemini API key for the worker (Enter to skip)"
  if ($value) { AddSecretFromText "gemini-api-key" $value }
}

Write-Host "== Secret access (per secret, per service account)"
foreach ($name in $ApiSecrets) {
  G secrets add-iam-policy-binding $name "--member=serviceAccount:$ApiSa@$Project.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor | Out-Null
}
foreach ($name in $WorkerSecrets) {
  G secrets add-iam-policy-binding $name "--member=serviceAccount:$WorkerSa@$Project.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor | Out-Null
}

Write-Host ""
Write-Host "Done. Next: copy deploy\env.production.yaml.example to deploy\env.production.yaml, edit it, then run deploy\deploy.ps1."
Write-Host "Also deny client access to Firestore: cd backend\deploy; firebase deploy --only firestore --project $Project"
