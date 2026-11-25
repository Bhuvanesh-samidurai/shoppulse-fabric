<#
.SYNOPSIS
  ShopPulse platform bootstrap (issue #1, part C). Idempotent: safe to re-run.

.DESCRIPTION
  Runs as Bhuvanesh (az login as the tenant admin). Creates per SPEC §4 / §4a:
    1. Entra security group sg-shoppulse-sp
    2. 8 app registrations + service principals (5 agents, 3 CI), added to the group
    3. Client secrets: agents -> infra/secrets/<name>.env (git-ignored);
                       CI     -> GitHub secrets (prod only in environment 'prod')
    4. 11 Fabric workspaces on the capacity
    5. Workspace role assignments
    6. Verification: logs in as each agent SP and lists the workspaces it can see

  Item-level shares (Gold/Silver ReadAll for BI and DS) are NOT done here; they need
  the lakehouses to exist (see #2 / #4).

.EXAMPLE
  .\infra\bootstrap\bootstrap.ps1 -DryRun      # read-only: shows what would change
  .\infra\bootstrap\bootstrap.ps1              # apply
  .\infra\bootstrap\bootstrap.ps1 -VerifyOnly  # only step 6
#>
[CmdletBinding()]
param(
    [string]$CapacityName = "fabcapacity1",
    [string]$Repo = "Bhuvanesh-samidurai/shoppulse-fabric",
    [int]$SecretYears = 1,
    [switch]$DryRun,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot   = Resolve-Path "$PSScriptRoot\..\.."
$SecretsDir = Join-Path $RepoRoot "infra\secrets"
$Az = (Get-Command az.cmd -ErrorAction SilentlyContinue).Source
if (-not $Az) { $Az = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd" }
$FabricApi = "https://api.fabric.microsoft.com/v1"
$Group = "sg-shoppulse-sp"

# ---------------------------------------------------------------- config (SPEC §4a)
$Agents = @("mgr", "de1", "de2", "bi", "ds")
$Ci     = @("dev", "test", "prod")
$SpNames = @($Agents | ForEach-Object { "sp-agent-$_" }) + @($Ci | ForEach-Object { "sp-ci-$_" })

# workspace -> @{ principal-name = role }   (Bhuvanesh is Admin on all as creator)
$Workspaces = [ordered]@{
    "shoppulse-feat-mgr"  = @{ "sp-agent-mgr" = "Admin" }
    "shoppulse-feat-de1"  = @{ "sp-agent-de1" = "Admin"; "sp-agent-mgr" = "Member" }
    "shoppulse-feat-de2"  = @{ "sp-agent-de2" = "Admin"; "sp-agent-mgr" = "Member" }
    "shoppulse-feat-bi"   = @{ "sp-agent-bi"  = "Admin" }
    "shoppulse-feat-ds"   = @{ "sp-agent-ds"  = "Admin" }
    # Dev: CI writes; DE agents read. BI/DS get item-level shares later (Gold / Silver only).
    "shoppulse-data-dev"  = @{ "sp-ci-dev" = "Member"; "sp-agent-mgr" = "Viewer"; "sp-agent-de1" = "Viewer"; "sp-agent-de2" = "Viewer" }
    "shoppulse-bi-dev"    = @{ "sp-ci-dev" = "Member"; "sp-agent-mgr" = "Viewer"; "sp-agent-bi" = "Viewer"; "sp-agent-ds" = "Viewer" }
    # Test: CI writes; every agent reads.
    "shoppulse-data-test" = @{ "sp-ci-test" = "Member"; "sp-agent-mgr" = "Viewer"; "sp-agent-de1" = "Viewer"; "sp-agent-de2" = "Viewer"; "sp-agent-bi" = "Viewer"; "sp-agent-ds" = "Viewer" }
    "shoppulse-bi-test"   = @{ "sp-ci-test" = "Member"; "sp-agent-mgr" = "Viewer"; "sp-agent-de1" = "Viewer"; "sp-agent-de2" = "Viewer"; "sp-agent-bi" = "Viewer"; "sp-agent-ds" = "Viewer" }
    # Prod: Bhuvanesh + prod CI only.
    "shoppulse-data-prod" = @{ "sp-ci-prod" = "Admin" }
    "shoppulse-bi-prod"   = @{ "sp-ci-prod" = "Admin" }
}

# ---------------------------------------------------------------- helpers
function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Plan($msg) { if ($DryRun) { Say "  [dry-run] would $msg" Yellow } else { Say "  -> $msg" Cyan } }

function AzJson {
    $out = & $Az @args -o json 2>$null
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed (exit $LASTEXITCODE)" }
    if ($out) { ($out -join "`n") | ConvertFrom-Json } else { $null }
}

$script:FabricToken = $null
function Fabric([string]$Method, [string]$Path, $Body = $null) {
    if (-not $script:FabricToken) {
        $script:FabricToken = & $Az account get-access-token --resource "https://api.fabric.microsoft.com" --query accessToken -o tsv
        if ($LASTEXITCODE -ne 0) { throw "Could not get a Fabric token. Run: az login" }
    }
    $p = @{ Method = $Method; Uri = "$FabricApi/$Path"; Headers = @{ Authorization = "Bearer $script:FabricToken" } }
    if ($Body) { $p.ContentType = "application/json"; $p.Body = ($Body | ConvertTo-Json -Depth 6) }
    Invoke-RestMethod @p
}

function FabricList([string]$Path) {
    $items = @(); $uri = $Path
    do {
        $r = Fabric GET $uri
        $items += $r.value
        $uri = if ($r.continuationToken) { "$Path`?continuationToken=$([uri]::EscapeDataString($r.continuationToken))" } else { $null }
    } while ($uri)
    $items
}

# ---------------------------------------------------------------- 0. who am I
$acct = AzJson account show
$TenantId = $acct.tenantId
Say "Tenant : $($acct.tenantDefaultDomain) ($TenantId)" Green
Say "User   : $($acct.user.name)" Green
if ($DryRun) { Say "DRY RUN: nothing will be changed." Yellow }

function Verify {
    Say "`n== 6. Verify: what each agent SP can see" Green
    foreach ($a in $Agents) {
        $f = Join-Path $SecretsDir "sp-agent-$a.env"
        if (-not (Test-Path $f)) { Say "  sp-agent-${a}: no secret file, skipped" Yellow; continue }
        $kv = @{}; Get-Content $f | Where-Object { $_ -match '^\s*([A-Z_]+)=(.*)$' } | ForEach-Object { $kv[$Matches[1]] = $Matches[2] }
        try {
            $tok = Invoke-RestMethod -Method POST -Uri "https://login.microsoftonline.com/$($kv.AZURE_TENANT_ID)/oauth2/v2.0/token" -Body @{
                grant_type = "client_credentials"; client_id = $kv.AZURE_CLIENT_ID; client_secret = $kv.AZURE_CLIENT_SECRET
                scope = "https://api.fabric.microsoft.com/.default" }
            $ws = Invoke-RestMethod -Uri "$FabricApi/workspaces" -Headers @{ Authorization = "Bearer $($tok.access_token)" }
            $names = ($ws.value | Sort-Object displayName | ForEach-Object displayName) -join ", "
            $expected = ($Workspaces.Keys | Where-Object { $Workspaces[$_].ContainsKey("sp-agent-$a") } | Sort-Object) -join ", "
            $ok = $names -eq $expected
            Say ("  sp-agent-{0}: {1}" -f $a, $names) ($(if ($ok) { "Green" } else { "Red" }))
            if (-not $ok) { Say "      expected: $expected" Red }
        } catch {
            Say "  sp-agent-${a}: FAILED - $($_.Exception.Message)" Red
            Say "      (new SPs can take a few minutes; check tenant setting 'Service principals can use Fabric APIs' includes $Group)" Red
        }
    }
}

if ($VerifyOnly) { Verify; return }

# ---------------------------------------------------------------- 1. group
Say "`n== 1. Security group $Group" Green
$grp = AzJson ad group list --filter "displayName eq '$Group'"
if ($grp) { $GroupId = $grp[0].id; Say "  exists ($GroupId)" }
else {
    Plan "create group $Group"
    if (-not $DryRun) { $GroupId = (AzJson ad group create --display-name $Group --mail-nickname $Group).id }
}

# ---------------------------------------------------------------- 2+3. apps, SPs, secrets
Say "`n== 2. App registrations + service principals" Green
New-Item -ItemType Directory -Force $SecretsDir | Out-Null
$existingGh = @{}
foreach ($scope in @("repo", "prod")) {
    $list = if ($scope -eq "repo") { gh secret list -R $Repo 2>$null } else { gh secret list -R $Repo --env prod 2>$null }
    $existingGh[$scope] = @($list | ForEach-Object { ($_ -split "\s+")[0] })
}
if (-not $DryRun) {
    if ($existingGh["repo"] -notcontains "FABRIC_TENANT_ID") { Plan "set GitHub secret FABRIC_TENANT_ID"; gh secret set FABRIC_TENANT_ID -R $Repo -b $TenantId | Out-Null }
    else { Say "  GitHub secret FABRIC_TENANT_ID exists" }
} elseif ($existingGh["repo"] -notcontains "FABRIC_TENANT_ID") { Plan "set GitHub secret FABRIC_TENANT_ID" }

$Sp = @{}   # name -> object id of service principal
foreach ($name in $SpNames) {
    $app = AzJson ad app list --display-name $name
    $app = $app | Where-Object { $_.displayName -eq $name } | Select-Object -First 1
    if (-not $app) {
        Plan "create app + service principal $name"
        if ($DryRun) { continue }
        $app = AzJson ad app create --display-name $name --sign-in-audience AzureADMyOrg
    } else { Say "  $name app exists ($($app.appId))" }

    # note: PowerShell variable names are case-insensitive, so don't reuse $Sp here
    $spObj = @(AzJson ad sp list --filter "appId eq '$($app.appId)'")
    if (-not $spObj) {
        Plan "create service principal for $name"
        if (-not $DryRun) { $spObj = @(AzJson ad sp create --id $app.appId) }
    }
    if ($spObj) { $Sp[$name] = $spObj[0].id }

    if ($GroupId -and $Sp[$name]) {
        $isMember = (AzJson ad group member check --group $GroupId --member-id $Sp[$name]).value
        if (-not $isMember) { Plan "add $name to $Group"; if (-not $DryRun) { & $Az ad group member add --group $GroupId --member-id $Sp[$name] 2>$null } }
    }

    # secrets
    if ($name -like "sp-agent-*") {
        $file = Join-Path $SecretsDir "$name.env"
        if (Test-Path $file) { Say "  $name secret file exists" }
        else {
            Plan "create client secret for $name -> infra/secrets/$name.env"
            if (-not $DryRun) {
                $cred = AzJson ad app credential reset --id $app.appId --display-name "bootstrap" --years $SecretYears --append
                @(
                    "# $name - generated by infra/bootstrap/bootstrap.ps1 on $(Get-Date -Format yyyy-MM-dd). Never commit."
                    "AZURE_TENANT_ID=$TenantId"
                    "AZURE_CLIENT_ID=$($app.appId)"
                    "AZURE_CLIENT_SECRET=$($cred.password)"
                    "FAB_TENANT_ID=$TenantId"
                    "FAB_SPN_CLIENT_ID=$($app.appId)"
                    "FAB_SPN_CLIENT_SECRET=$($cred.password)"
                ) | Set-Content -Encoding ascii $file
            }
        }
    } else {
        $envName = ($name -replace "^sp-ci-", "").ToUpper()
        $scope = if ($envName -eq "PROD") { "prod" } else { "repo" }
        $idKey = "FABRIC_CI_${envName}_CLIENT_ID"; $secKey = "FABRIC_CI_${envName}_CLIENT_SECRET"
        if ($existingGh[$scope] -contains $secKey) { Say "  $name GitHub secrets exist ($scope)" }
        else {
            Plan "create client secret for $name -> GitHub secrets ($scope)"
            if (-not $DryRun) {
                $cred = AzJson ad app credential reset --id $app.appId --display-name "github-$($envName.ToLower())" --years $SecretYears --append
                $envArg = if ($scope -eq "prod") { @("--env", "prod") } else { @() }
                gh secret set $idKey  -R $Repo @envArg -b $app.appId | Out-Null
                gh secret set $secKey -R $Repo @envArg -b $cred.password | Out-Null
                if ($scope -eq "prod") { gh secret set FABRIC_TENANT_ID -R $Repo --env prod -b $TenantId | Out-Null }
            }
        }
    }
}

# ---------------------------------------------------------------- 4. workspaces
Say "`n== 4. Workspaces on capacity $CapacityName" Green
$cap = FabricList "capacities" | Where-Object { $_.displayName -eq $CapacityName } | Select-Object -First 1
if (-not $cap) { throw "Capacity '$CapacityName' not found or not visible to $($acct.user.name). Is it running (not paused) and are you a capacity admin?" }
Say "  capacity $($cap.displayName): $($cap.sku), state $($cap.state)"
if ($cap.state -ne "Active") {
    if ($DryRun) { Say "  capacity is $($cap.state): resume it in the Azure portal before the real run" Yellow }
    else { throw "Capacity is $($cap.state). Resume it in the Azure portal and re-run." }
}

$allWs = FabricList "workspaces"
$WsId = @{}
foreach ($ws in $Workspaces.Keys) {
    $hit = $allWs | Where-Object { $_.displayName -eq $ws } | Select-Object -First 1
    if ($hit) {
        $WsId[$ws] = $hit.id
        if ($hit.capacityId -ne $cap.id) {
            Plan "assign $ws to $CapacityName"
            if (-not $DryRun) { Fabric POST "workspaces/$($hit.id)/assignToCapacity" @{ capacityId = $cap.id } | Out-Null }
        } else { Say "  $ws exists" }
    } else {
        Plan "create workspace $ws"
        if (-not $DryRun) {
            $new = Fabric POST "workspaces" @{ displayName = $ws; capacityId = $cap.id; description = "ShopPulse - see SPEC.md §4" }
            $WsId[$ws] = $new.id
        }
    }
}

# ---------------------------------------------------------------- 5. role assignments
Say "`n== 5. Workspace roles" Green
foreach ($ws in $Workspaces.Keys) {
    if (-not $WsId[$ws]) { foreach ($p in $Workspaces[$ws].Keys) { Plan "grant $p $($Workspaces[$ws][$p]) on $ws" }; continue }
    $current = FabricList "workspaces/$($WsId[$ws])/roleAssignments"
    foreach ($p in $Workspaces[$ws].Keys) {
        $role = $Workspaces[$ws][$p]
        $oid = $Sp[$p]
        if (-not $oid) { Plan "grant $p $role on $ws (after SP exists)"; continue }
        $have = $current | Where-Object { $_.principal.id -eq $oid } | Select-Object -First 1
        if (-not $have) {
            Plan "grant $p $role on $ws"
            if (-not $DryRun) { Fabric POST "workspaces/$($WsId[$ws])/roleAssignments" @{ principal = @{ id = $oid; type = "ServicePrincipal" }; role = $role } | Out-Null }
        } elseif ($have.role -ne $role) {
            Plan "change $p on $ws from $($have.role) to $role"
            if (-not $DryRun) { Fabric PATCH "workspaces/$($WsId[$ws])/roleAssignments/$($have.id)" @{ role = $role } | Out-Null }
        } else { Say "  $ws : $p = $role" }
    }
}

if ($DryRun) { Say "`nDry run finished. Re-run without -DryRun to apply." Yellow; return }
Say "`nWaiting 60 s for new service principals to propagate..." Gray
Start-Sleep -Seconds 60
Verify
Say "`nDone. Agent secrets: infra/secrets/ (git-ignored). CI secrets: GitHub repo + 'prod' environment." Green
