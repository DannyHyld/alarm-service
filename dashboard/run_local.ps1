# Starter dashboardet på din egen computer mod alarm-servicen i Azure.
# Operatørnøglen hentes fra Key Vault og lever kun i denne proces. Kræver az login.
$env:API_URL = "http://alarm-service-uj0vm.denmarkeast.cloudapp.azure.com:5000"
$env:OPERATOR_API_KEY = (az keyvault secret show --vault-name kv-alarm-uj0vm -n operator-api-key --query value -o tsv)
if (-not $env:DASHBOARD_PORT) { $env:DASHBOARD_PORT = "8090" }  # 8080 bruges af et andet projekt
py -I "$PSScriptRoot\server.py"
