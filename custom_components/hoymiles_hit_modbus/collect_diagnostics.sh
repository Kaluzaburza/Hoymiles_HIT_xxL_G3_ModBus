#!/usr/bin/env sh
# Create one privacy-filtered support archive from a Home Assistant OS terminal.

set -eu
umask 077

OUTPUT_ROOT="${1:-/config/hoymiles_diagnostics}"
case "$OUTPUT_ROOT" in
  /config|/config/*) ;;
  *)
    echo "ERROR: output directory must be inside /config" >&2
    exit 2
    ;;
esac

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d)"
ARCHIVE="$OUTPUT_ROOT/hoymiles_diagnostics_$STAMP.tar.gz"
mkdir -p "$OUTPUT_ROOT"
trap 'rm -rf "$WORK_DIR"' EXIT HUP INT TERM

redact_stream() {
  sed -E \
    -e 's#https?://[^[:space:]"<>]+#[REDACTED_URL]#g' \
    -e 's#([0-9]{1,3}\.){3}[0-9]{1,3}#[REDACTED_IP]#g' \
    -e 's#([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}#[REDACTED_MAC]#g' \
    -e 's#[^[:space:]@]+@[^[:space:]@]+\.[[:alnum:]]+#[REDACTED_EMAIL]#g' \
    -e 's#([Aa]uthorization:[[:space:]]*[Bb]earer[[:space:]]+)[^[:space:]]+#\1[REDACTED_SECRET]#g' \
    -e 's#^([[:space:]]*[^:]*([Pp]assword|[Tt]oken|[Ss]ecret|[Aa]pi[_ -]?[Kk]ey|[Kk]ey|[Ss][Ss][Ii][Dd]|[Ss]erial|[Uu]sername|[Hh]ostname|[Ll]atitude|[Ll]ongitude)[^:]*:).*#\1 [REDACTED_SECRET]#'
}

capture_command() {
  destination="$1"
  shift
  if "$@" 2>&1 | redact_stream > "$WORK_DIR/$destination"; then
    return 0
  fi
  printf '%s\n' "Command unavailable or failed; see the other report sections." \
    > "$WORK_DIR/$destination"
}

{
  printf '%s\n' "Hoymiles HIT xxL G3 Modbus support archive"
  printf 'Generated UTC: %s\n' "$STAMP"
  printf '%s\n' "The archive was automatically redacted. Review it before posting publicly."
  printf '%s\n' "Also include the exact local time of the fault and what the inverter did."
  printf '%s\n' "Native Home Assistant diagnostics are included when the API permits it."
} > "$WORK_DIR/README.txt"

capture_command "ha_info.txt" ha info
capture_command "core_info.txt" ha core info
capture_command "supervisor_info.txt" ha supervisor info
capture_command "host_info.txt" ha host info
capture_command "resolution_info.txt" ha resolution info
capture_command "storage.txt" df -h
capture_command "memory.txt" free -h

if command -v ha >/dev/null 2>&1; then
  ha core logs 2>&1 \
    | grep -Ei 'hoymiles|esphome|modbus|rce|rcm|tariff|taryf|ems|SocketClosedAPIError' \
    | tail -n 2500 \
    | redact_stream > "$WORK_DIR/home_assistant_relevant_logs.txt" || true
  ha addons logs 5c53de3b_esphome 2>&1 \
    | tail -n 2500 \
    | redact_stream > "$WORK_DIR/esphome_addon_logs.txt" || true
fi

if [ -r /config/custom_components/hoymiles_hit_modbus/manifest.json ]; then
  cp /config/custom_components/hoymiles_hit_modbus/manifest.json \
    "$WORK_DIR/integration_manifest.json"
fi
if [ -r /config/esphome/hoymiles-inverter.yaml ]; then
  redact_stream < /config/esphome/hoymiles-inverter.yaml \
    > "$WORK_DIR/esphome_configuration_redacted.yaml"
fi

# Ask Core for the integration's native diagnostic JSON. Only entry IDs are
# read from .storage; the configuration database itself is never copied.
if command -v jq >/dev/null 2>&1 \
  && command -v curl >/dev/null 2>&1 \
  && [ -n "${SUPERVISOR_TOKEN:-}" ] \
  && [ -r /config/.storage/core.config_entries ]; then
  entry_number=0
  jq -r '.data.entries[] | select(.domain == "hoymiles_hit_modbus") | .entry_id' \
    /config/.storage/core.config_entries \
    | while IFS= read -r entry_id; do
        [ -n "$entry_id" ] || continue
        entry_number=$((entry_number + 1))
        destination="$WORK_DIR/native_diagnostics_$entry_number.json"
        if ! curl -fsS \
          -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
          "http://supervisor/core/api/diagnostics/config_entry/$entry_id" \
          | redact_stream > "$destination"; then
          rm -f "$destination"
        fi
      done

  curl -fsS \
    -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
    http://supervisor/core/api/states 2>/dev/null \
    | jq '[.[] | select(.entity_id | test("^(automation|binary_sensor|button|input_boolean|input_datetime|input_number|input_select|input_text|number|select|sensor|switch|timer)\\.hoymiles")) | {entity_id, state, last_changed, last_updated, attributes}]' \
    | redact_stream > "$WORK_DIR/hoymiles_states_redacted.txt" || true
fi

tar -czf "$ARCHIVE" -C "$WORK_DIR" .
printf '\nDiagnostics ready:\n%s\n' "$ARCHIVE"
printf '%s\n' "Attach this archive and the fault time to the GitHub issue."
