<#
Opretter produktionsmiljøet i Azure og kobler det til GitHub:
  1. Resource group
  2. Key Vault (RBAC) med fem tilfældige secrets
  3. Ubuntu-VM med managed identity, Docker (cloud-init) og åbne porte 22, 5000 og 8025
  4. VM'ens identity får "Key Vault Secrets User" (kun læse)
  5. GitHub Secrets og variablen DEPLOY_ENABLED, så pipelinen kan deploye

Kør efter "az login" og "gh auth login". Det bruger af Azure for Students-kreditten.
Alt ligger i én resource group og slettes igen med:
    az group delete -n rg-alarm-service --yes

Eksempel:
    .\infra\azure-setup.ps1 -Repo dannyhyld/alarm-service
#>
param(
    # Skolens Azure-politik tillader kun: belgiumcentral, denmarkeast, germanywestcentral,
    # norwayeast og switzerlandnorth. Denmark East holder data i Danmark.
    [string]$Location = "denmarkeast",
    [string]$ResourceGroup = "rg-alarm-service",
    [string]$VmName = "vm-alarm-service",
    [string]$VmSize = "Standard_B2s",
    [string]$Repo = "",
    [string]$Suffix = ""
)

# "Continue": Windows PowerShell 5.1 gør ellers enhver linje på stderr fra az
# (også advarsler) til et hårdt stop. Fejl fanges i stedet via exit-koden i Invoke-Az.
$ErrorActionPreference = "Continue"

function Test-Az {
    # Returnerer $true, hvis az-kaldet lykkes (bruges til at springe trin over, der allerede er gjort).
    & az @args -o none 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-Az {
    # Kør et az-kald og stop scriptet, hvis det fejler.
    & az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') fejlede" }
}

function New-RandomSecret {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

if (-not $Suffix) {
    $Suffix = -join ((97..122) + (48..57) | Get-Random -Count 5 | ForEach-Object { [char]$_ })
}
$Vault = "kv-alarm-$Suffix"
$DnsLabel = "alarm-service-$Suffix"
$Root = Split-Path $PSScriptRoot -Parent
$KeyPath = Join-Path $HOME ".ssh\alarm-service-deploy"

Write-Host "== 0. Registrér de Azure-tjenester, et nyt abonnement ikke har slået til"
foreach ($ns in "Microsoft.KeyVault", "Microsoft.Compute", "Microsoft.Network") {
    $state = az provider show --namespace $ns --query registrationState -o tsv
    if ($state -ne "Registered") {
        Write-Host "   Registrerer $ns (kan tage et par minutter)"
        Invoke-Az provider register --namespace $ns --wait -o none
    } else {
        Write-Host "   $ns er registreret"
    }
}

Write-Host "== 1. Resource group $ResourceGroup i $Location"
Invoke-Az group create -n $ResourceGroup -l $Location -o none

Write-Host "== 2. Key Vault $Vault"
if (Test-Az keyvault show -n $Vault) {
    Write-Host "   findes allerede, genbruges"
} else {
    Invoke-Az keyvault create -n $Vault -g $ResourceGroup -l $Location --enable-rbac-authorization true -o none
}
$me = az ad signed-in-user show --query id -o tsv
$vaultId = az keyvault show -n $Vault --query id -o tsv
# OBS: ingen parenteser i --query. az er en .cmd-fil, og cmd.exe læser "(" og ")" som kode.
$har = az role assignment list --assignee $me --role "Key Vault Secrets Officer" --scope $vaultId --query "[].id" -o tsv
if (-not $har) {
    Invoke-Az role assignment create --assignee-object-id $me --assignee-principal-type User `
        --role "Key Vault Secrets Officer" --scope $vaultId -o none
}

Write-Host "   Gemmer secrets (rettigheder kan tage et par minutter om at slå igennem)"
foreach ($name in "device-api-key", "operator-api-key", "mysql-password", "mysql-root-password", "mailpit-password", "grafana-admin-password") {
    # En eksisterende secret må ikke overskrives: MySQL er startet med den gamle adgangskode.
    if (Test-Az keyvault secret show --vault-name $Vault -n $name) { Write-Host "   $name findes allerede"; continue }
    $value = New-RandomSecret
    $ok = $false
    for ($i = 1; $i -le 20; $i++) {
        az keyvault secret set --vault-name $Vault -n $name --value $value -o none 2>$null
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        Start-Sleep -Seconds 15
    }
    if (-not $ok) { throw "Kunne ikke gemme $name i Key Vault" }
    Write-Host "   $name gemt"
}

Write-Host "== 3. SSH-nøgle til deploy ($KeyPath)"
if (-not (Test-Path $KeyPath)) {
    # Windows PowerShell kan ikke sende en tom adgangskode til ssh-keygen. Lav nøglen i Git Bash:
    #   ssh-keygen -t ed25519 -f ~/.ssh/alarm-service-deploy -N "" -C alarm-service-deploy
    throw "Mangler SSH-nøglen $KeyPath. Lav den først (se kommentaren i scriptet)."
}

Write-Host "== 3. VM $VmName ($VmSize, Ubuntu 24.04)"
if (Test-Az vm show -g $ResourceGroup -n $VmName) {
    Write-Host "   findes allerede, genbruges"
} else {
Invoke-Az vm create -g $ResourceGroup -n $VmName --image Ubuntu2404 --size $VmSize `
    --admin-username azureuser --ssh-key-values "$KeyPath.pub" `
    --assign-identity --public-ip-sku Standard --public-ip-address-dns-name $DnsLabel `
    --custom-data (Join-Path $Root "infra\cloud-init.yaml") --nsg-rule SSH -o none
}
Invoke-Az vm open-port -g $ResourceGroup -n $VmName --port 5000 --priority 1010 -o none
Invoke-Az vm open-port -g $ResourceGroup -n $VmName --port 8025 --priority 1020 -o none
Invoke-Az vm open-port -g $ResourceGroup -n $VmName --port 3000 --priority 1030 -o none

Write-Host "== 4. VM'ens identity får læseadgang til Key Vault"
$principal = az vm show -g $ResourceGroup -n $VmName --query identity.principalId -o tsv
$har = az role assignment list --assignee $principal --role "Key Vault Secrets User" --scope $vaultId --query "[].id" -o tsv
if (-not $har) {
    Invoke-Az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal `
        --role "Key Vault Secrets User" --scope $vaultId -o none
}

$fqdn = az vm show -d -g $ResourceGroup -n $VmName --query fqdns -o tsv
Write-Host "   Venter på, at cloud-init har installeret Docker"
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    ssh -i $KeyPath -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "azureuser@$fqdn" `
        "cloud-init status --wait > /dev/null; docker --version && docker compose version"
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 10
}
if (-not $ready) { throw "VM'en svarer ikke på SSH, eller Docker blev ikke installeret" }
# Host key fra den SSH-forbindelse, der lige lykkedes. Windows' ssh-keyscan kan ikke
# forhandle med OpenSSH 9.6 på Ubuntu 24.04 (choose_kex: sntrup761x25519).
$knownHosts = (Get-Content (Join-Path $HOME ".ssh\known_hosts") |
    Where-Object { $_ -like "$fqdn ssh-ed25519 *" } | Select-Object -First 1)
if (-not $knownHosts) { throw "Kunne ikke hente VM'ens host key" }

if ($Repo) {
    Write-Host "== 5. GitHub Secrets i $Repo"
    gh secret set VM_HOST -R $Repo -b $fqdn
    gh secret set VM_USER -R $Repo -b "azureuser"
    gh secret set KEY_VAULT_NAME -R $Repo -b $Vault
    # Windows PowerShell 5.1 tilføjer CRLF, når tekst pipes til et program, og så
    # passer host key ikke ("Host key verification failed"). cmd.exe's "<" sender
    # filen byte for byte.
    cmd /c "gh secret set VM_SSH_KEY -R $Repo < `"$KeyPath`""
    $khFile = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($khFile, "$knownHosts`n")
    cmd /c "gh secret set VM_KNOWN_HOSTS -R $Repo < `"$khFile`""
    Remove-Item $khFile
    gh variable set DEPLOY_ENABLED -R $Repo -b "true"
}

Write-Host ""
Write-Host "Færdig."
Write-Host "  API:        http://${fqdn}:5000/docs"
Write-Host "  Mailpit:    http://${fqdn}:8025  (bruger: vagt, adgangskode: secret mailpit-password)"
Write-Host "  Grafana:    http://${fqdn}:3000  (bruger: admin, adgangskode: secret grafana-admin-password)"
Write-Host "  Key Vault:  $Vault"
Write-Host "  SSH:        ssh -i $KeyPath azureuser@$fqdn"
Write-Host "Hent enhedens nøgle til Postman med:"
Write-Host "  az keyvault secret show --vault-name $Vault -n device-api-key --query value -o tsv"
