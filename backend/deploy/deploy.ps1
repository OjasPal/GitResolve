# Build the API image and deploy it to Cloud Run (TRD section 11).
# Usage (PowerShell): .\backend\deploy\deploy.ps1 -Project <gcp-project-id> [-Region asia-south1] [-Tag <tag>]
# Needs deploy\env.production.yaml (copy of env.production.yaml.example) and a completed setup.ps1.
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string]$Region = "asia-south1",
  [string]$Tag = (Get-Date -Format "yyyyMMdd-HHmmss")
)
$ErrorActionPreference = "Stop"
$Service = "gitresolve-api"
$Here = $PSScriptRoot
$Backend = Split-Path $Here -Parent
$EnvFile = Join-Path $Here "env.production.yaml"
$Image = "$Region-docker.pkg.dev/$Project/gitresolve/api:$Tag"
if (-not (Test-Path $EnvFile)) { throw "Missing $EnvFile (copy env.production.yaml.example)" }

function G { & gcloud @args; if ($LASTEXITCODE -ne 0) { throw "gcloud $($args -join ' ') failed" } }

$Number = (& gcloud projects describe $Project "--format=value(projectNumber)").Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read the project number." }
$Url = "https://$Service-$Number.$Region.run.app"
$Rendered = [System.IO.Path]::GetTempFileName()
try {
  $text = (Get-Content $EnvFile -Raw).Replace('"AUTO"', "`"$Url/api/v1/auth/github/callback`"")
  [System.IO.File]::WriteAllText($Rendered, $text, (New-Object System.Text.UTF8Encoding($false)))

  Write-Host "== Building $Image"
  G builds submit $Backend --project $Project --tag $Image --quiet

  Write-Host "== Deploying $Service"
  G run deploy $Service --project $Project --region $Region --image $Image --quiet `
    --service-account "gitresolve-api@$Project.iam.gserviceaccount.com" `
    --allow-unauthenticated `
    --min-instances 0 --max-instances 2 --memory 512Mi --cpu 1 --concurrency 40 --timeout 60 --port 8080 `
    --env-vars-file $Rendered `
    --set-secrets "SESSION_JWT_SECRET=session-jwt-secret:latest,GITHUB_APP_CLIENT_SECRET=github-app-client-secret:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest"
} finally { Remove-Item $Rendered -Force -ErrorAction SilentlyContinue }

$Live = (& gcloud run services describe $Service --project $Project --region $Region "--format=value(status.url)").Trim()
Write-Host "== Service URL: $Live"
if ($Live -ne $Url) { Write-Warning "URL differs from the callback host used ($Url). Set GITHUB_APP_CALLBACK_URL explicitly and redeploy." }
Write-Host "== Health check"
Invoke-RestMethod "$Live/health" | ConvertTo-Json -Compress
Write-Host "GitHub App callback URL to register: $Live/api/v1/auth/github/callback"
Write-Host "Set NEXT_PUBLIC_API_BASE_URL=$Live in Vercel."
