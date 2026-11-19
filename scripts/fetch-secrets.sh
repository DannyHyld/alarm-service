#!/usr/bin/env bash
# Kører på Azure VM'en ved hver deploy.
# Henter secrets fra Azure Key Vault med VM'ens managed identity og skriver .env,
# som kun ejeren kan læse. VM'en har rollen "Key Vault Secrets User" (kun læse).
#
# Brug: bash fetch-secrets.sh <vault-navn> [.env]
set -euo pipefail

VAULT_NAME="${1:?Brug: fetch-secrets.sh <vault-navn> [output-fil]}"
OUT="${2:-.env}"

TOKEN=$(curl -sf -H "Metadata: true" \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

get_secret() {
  curl -sf -H "Authorization: Bearer ${TOKEN}" \
    "https://${VAULT_NAME}.vault.azure.net/secrets/$1?api-version=7.4" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])'
}

umask 077
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
{
  echo "DEVICE_API_KEY=$(get_secret device-api-key)"
  echo "OPERATOR_API_KEY=$(get_secret operator-api-key)"
  echo "MYSQL_PASSWORD=$(get_secret mysql-password)"
  echo "MYSQL_ROOT_PASSWORD=$(get_secret mysql-root-password)"
  echo "MAILPIT_PASSWORD=$(get_secret mailpit-password)"
  echo "GRAFANA_ADMIN_PASSWORD=$(get_secret grafana-admin-password)"
  # Ikke-hemmelig konfiguration
  echo "MYSQL_DATABASE=alarm_service"
  echo "MYSQL_USER=alarm_app"
  echo "ALARM_EMAIL_FROM=alarm@intelligent-iot.example"
  echo "ALARM_EMAIL_TO=vagt@intelligent-iot.example"
  echo "RATE_LIMIT=120 per minute"
  echo "LOG_LEVEL=INFO"
} > "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
echo "Skrev $(wc -l < "$OUT") linjer til $OUT (værdierne vises ikke)"
