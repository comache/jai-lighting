#!/usr/bin/env bash
# User-run, read-only HA configuration inspection. Only export files are written.
set +x
set -Eeuo pipefail
umask 077

for jai_tool in jq curl tar sha256sum mktemp; do
  command -v "$jai_tool" >/dev/null || { echo "Missing required tool: $jai_tool" >&2; exit 1; }
done
JAI_CONFIG=${JAI_EXPORT_CONFIG_DIR:-/config}
if [[ ! -d "$JAI_CONFIG/.storage" && -z ${JAI_EXPORT_CONFIG_DIR:-} ]]; then
  JAI_CONFIG=/homeassistant
fi
[[ -d "$JAI_CONFIG/.storage" ]] || { echo 'HA configuration directory not found.' >&2; exit 1; }
JAI_DEST=${JAI_EXPORT_OUTPUT_DIR:-/share}
[[ -d "$JAI_DEST" && -w "$JAI_DEST" ]] || { echo 'Output directory is not writable.' >&2; exit 1; }
JAI_WORK=$(mktemp -d)
JAI_OUT=$(mktemp -d "$JAI_DEST/jai-bubble-export-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
JAI_ARCHIVE="$JAI_OUT.tar.gz"
trap 'rm -rf -- "$JAI_WORK"' EXIT
trap 'echo "Export failed. Do not share incomplete output. No HA configuration was changed." >&2' ERR
mkdir "$JAI_OUT/dashboards"
JAI_REPORT="$JAI_OUT/report.txt"
cat >"$JAI_REPORT" <<'TEXT'
J'ai Control Bubble: read-only diagnostic export (not a backup).
Sources: selected local JSON stores; optional read-only Core API GET /states.
No service calls, config writes, restart, lighting or media commands.
No auth/config-entry/credential stores, secrets.yaml, logs or database exported.
Dashboards are SANITIZED: unknown custom fields, free text/code/templates,
external URLs and non-lighting action payloads are deliberately omitted.
Registry IDs, entity IDs, topology, names and supported capabilities are retained.
Live API failure does not prevent a useful saved-configuration export.
Files are individual reads, not an atomic live snapshot; avoid editing during export.
TEXT
date -u '+Collected at: %Y-%m-%dT%H:%M:%SZ' >>"$JAI_REPORT"

# Only filtered data reaches disk. No unfiltered source copies or HTTP responses.
cat >"$JAI_WORK/filter.jq" <<'JQ'
def sensitive:
  test("password|passwd|secret|token|credential|authorization|cookie|private.?key|api.?key|client.?secret|oauth|^auth$"; "i");
def textclean:
  (if startswith("/") and (startswith("//") | not) then split("?")[0] | split("#")[0] else . end) |
  if test("(?i)bearer\\s|password\\s*[:=]|secret\\s*[:=]|token\\s*[:=]|BEGIN .*PRIVATE KEY|eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+|!secret|\\[\\[\\[|\\{\\{") then "[REDACTED]"
  elif test("(?i)[a-z][a-z0-9+.-]*://|^//|^data:") then "[EXTERNAL URL OMITTED]"
  elif startswith("/") then split("?")[0] | split("#")[0]
  elif length > 512 then "[LONG TEXT OMITTED]"
  else . end;
def clean:
  if type == "object" then with_entries(
    if (.key | sensitive) then .value = "[REDACTED]" else .value |= clean end)
  elif type == "array" then map(clean)
  elif type == "string" then textclean
  else . end;
def pick($keys): with_entries(select(.key as $k | $keys | index($k)));
def dashkeys: [
  "config","version","revision","title","name","type","views","sections","cards",
  "card","entities","entity","entity_id","attribute","icon","path","url_path",
  "id","mode","show_in_sidebar","require_admin","filename","max_columns","dense_section_placement",
  "column_span","grid_options","columns","rows","square","layout","layout_options",
  "view_layout","grid_column","grid_row","grid_area","badges","header","footer",
  "heading","heading_style","heading_icon","theme","background","panel","subview",
  "visible","visibility","condition","conditions","state","state_not","numeric_state",
  "above","below","screen","media_query","tap_action","hold_action","double_tap_action",
  "action","service","perform_action","target","navigation_path","navigation_replace",
  "confirmation","enabled","icon_tap_action","button_action","button_type","card_type",
  "hash","popup_mode","show_previous_button","close_on_click","sub_button","main","bottom",
  "show_name","show_state","show_icon","show_attribute","show_last_changed","show_last_updated",
  "show_background","show_header","show_slider","show_buttons","show_control","show_brightness_control",
  "show_color_control","show_color_temp_control","slider_live_update","read_only_slider",
  "min_value","max_value","step","relative_slide","allow_light_slider_to_0","light_slider_type",
  "card_layout","rows","width","height","min_height","max_height","scrolling_effect",
  "hide","hide_header","hide_sidebar","kiosk_mode","non_admin_settings","admin_settings",
  "mobile_settings","ignore_entity","ignore_mobile_settings","sub_button_type","select_attribute",
  "icon_color","state_color","color","accent","size","features","features_position",
  "vertical","hide_state","collapse","collapsed","items","url","resource_type","resources",
  "sidebar","general","spotify","shortcuts","tabs","hidden","floor_id","quick","quick_brightness",
  "kind","moods","group_id","parent_group_id","source","source_list","volume_stateless"
];
def payload:
  if type == "object" then pick(["entity_id","brightness","brightness_pct","transition","color_temp_kelvin","rgb_color","hs_color","effect","preset_id","group_id","collection_id"]) | clean
  else "[ACTION PAYLOAD OMITTED]" end;
def dashboard:
  if type == "object" then
    . as $original |
    with_entries(select((.key as $k | dashkeys | index($k)) or .key == "data" or .key == "service_data")) |
    with_entries(if .key == "data" or .key == "service_data" then .value |= payload else .value |= dashboard end) |
    if ($original | keys | length) > (keys | length) then . + {"_omitted_fields": (($original | keys | length) - (keys | length))} else . end
  elif type == "array" then map(dashboard)
  else clean end;
def selectedstates:
  [.[] | select(.entity_id | test("^(light|media_player|scene|switch|input_boolean|number|input_number)\\.")) |
    {entity_id, state: (if (.state | IN("on","off","playing","paused","idle","standby","unavailable","unknown")) then .state else "[NON-POWER STATE OMITTED]" end),
     attributes: (.attributes | pick(["friendly_name","icon","supported_features","supported_color_modes","color_mode","brightness","min_color_temp_kelvin","max_color_temp_kelvin","effect_list","jai_group","group_id","parent_group_id","parent_entity_id","child_group_ids","child_entity_ids","direct_leaf_ids","direct_member_entity_ids","entity_id","descendant_leaf_count","depth","sibling_order","power_policy","master_brightness","master_brightness_pct","config_revision","unavailable_members","source","source_list","media_title","media_artist","media_album_name","volume_level","is_volume_muted","min","max","step","unit_of_measurement"]))} | clean];
JQ

jai_filter() { jq -L "$JAI_WORK" "include \"filter\"; $1"; }
jai_read() {
  local source=$1 output=$2 expression=$3
  if [[ -f "$source" && ! -L "$source" ]]; then
    if jai_filter "$expression" <"$source" >"$JAI_OUT/$output" 2>/dev/null; then
      printf 'OK: %s\n' "$output" >>"$JAI_REPORT"
    else
      rm -f "$JAI_OUT/$output"
      printf 'MISSING: could not parse %s\n' "${source##*/}" >>"$JAI_REPORT"
    fi
  else
    printf 'MISSING/NOT SAVED: %s\n' "${source##*/}" >>"$JAI_REPORT"
  fi
}

jai_read "$JAI_CONFIG/.storage/core.entity_registry" entities.json \
  '[.data.entities[] | {id,entity_id,platform,area_id,device_id,name,original_name,icon,disabled_by,hidden_by,labels} + (if .platform == "jai_lighting" then {unique_id} else {} end)] | clean'
jai_read "$JAI_CONFIG/.storage/core.device_registry" devices.json \
  '[.data.devices[] | {id,area_id,name,name_by_user,manufacturer,model,labels}] | clean'
jai_read "$JAI_CONFIG/.storage/core.area_registry" areas.json \
  '[.data.areas[] | {id,name,floor_id,aliases,labels}] | clean'
jai_read "$JAI_CONFIG/.storage/core.floor_registry" floors.json \
  '[.data.floors[] | {id,name,level,aliases}] | clean'
jai_read "$JAI_CONFIG/.storage/jai_lighting.state" jai-topology.json \
  '.data | pick(["schema_version","config_revision","state_revision","groups","leaves","presets","collections","runtime","capability_cache"]) | clean'
jai_read "$JAI_CONFIG/.storage/jai_lighting.dashboard" jai-control.json '.data | dashboard'
jai_read "$JAI_CONFIG/custom_components/jai_lighting/manifest.json" jai-version.json \
  'pick(["domain","name","version"]) | clean'
jai_read "$JAI_CONFIG/.storage/lovelace_dashboards" dashboard-index.json '.data.items | dashboard'
jai_read "$JAI_CONFIG/.storage/lovelace_resources" resources.json '.data.items | dashboard'

shopt -s nullglob
for jai_file in "$JAI_CONFIG/.storage/lovelace" "$JAI_CONFIG"/.storage/lovelace.*; do
  [[ -f "$jai_file" ]] || continue
  jai_read "$jai_file" "dashboards/${jai_file##*/}.json" '.data | dashboard'
done

# Optional YAML support, only with the known safe yq implementation.
# No HA YAML tags are executed. !include and !secret are reported, never resolved.
if command -v yq >/dev/null && yq --version 2>/dev/null | grep -qi 'mikefarah'; then
  jai_yaml() {
    local source=$1 output=$2 selector=$3
    [[ -f "$source" && ! -L "$source" ]] || return 0
    if yq eval -o=json "$selector" "$source" 2>/dev/null | jai_filter 'dashboard' >"$JAI_OUT/$output" 2>/dev/null; then
      printf 'OK: %s (YAML includes/tags not expanded)\n' "$output" >>"$JAI_REPORT"
    else
      rm -f "$JAI_OUT/$output"
      echo 'MISSING: unsupported YAML source' >>"$JAI_REPORT"
    fi
  }
  jai_yaml "$JAI_CONFIG/configuration.yaml" lovelace-yaml-settings.json '.lovelace'
  jai_yaml "$JAI_CONFIG/ui-lovelace.yaml" dashboards/ui-lovelace.json '.'
  if [[ -f "$JAI_OUT/lovelace-yaml-settings.json" ]]; then
    # The generic dashboard sanitizer omits arbitrary dashboard-map keys;
    # discover filenames directly, without exporting any unrelated configuration.
    while IFS= read -r jai_yaml_path; do
      case "$jai_yaml_path" in
        /*|*..*|*secrets*|*.storage*|*credentials*|*auth*) continue ;;
        *.yaml|*.yml) ;;
        *) continue ;;
      esac
      jai_yaml "$JAI_CONFIG/$jai_yaml_path" "dashboards/yaml-$(printf '%s' "$jai_yaml_path" | sha256sum | cut -c1-16).json" '.'
    done < <(yq eval -o=json '.lovelace.dashboards' "$JAI_CONFIG/configuration.yaml" 2>/dev/null | jq -r '.. | objects | .filename? // empty' 2>/dev/null)
  fi
else
  echo 'NOTE: YAML dashboard export unavailable (Mike Farah yq not installed); JSON storage dashboards collected.' >>"$JAI_REPORT"
fi

# HACS metadata only: never read .storage/hacs or any authentication stores.
jai_read "$JAI_CONFIG/.storage/hacs.repositories" custom-cards.json \
  '[.. | objects | select(.full_name? != null) | select(.category? == "plugin" or (.full_name | test("bubble|card|kiosk|lovelace";"i"))) | pick(["full_name","category","installed","installed_version","available_version","selected_tag","last_version","version","version_installed"]) | clean] | unique'
if [[ -f "$JAI_OUT/custom-cards.json" ]]; then
  jq '[.[] | select(.full_name | test("(^|/)bubble-card$";"i"))]' "$JAI_OUT/custom-cards.json" >"$JAI_OUT/bubble-version.json"
fi
printf 'relative_path\tsha256\n' >"$JAI_OUT/custom-card-files.tsv"
for jai_file in "$JAI_CONFIG"/www/community/*/*.js "$JAI_CONFIG"/www/*card*.js; do
  [[ -f "$jai_file" && ! -L "$jai_file" ]] || continue
  printf '%s\t%s\n' "${jai_file#"$JAI_CONFIG/"}" "$(sha256sum "$jai_file" | cut -d ' ' -f1)" >>"$JAI_OUT/custom-card-files.tsv"
done

# A GET is the only HTTP operation. Token travels over stdin, never argv/output.
if [[ ${JAI_EXPORT_OFFLINE:-0} != 1 && ${SUPERVISOR_TOKEN:-} =~ ^[A-Za-z0-9._-]+$ ]]; then
  if printf 'header = "Authorization: Bearer %s"\n' "$SUPERVISOR_TOKEN" |
    curl -q --config - --noproxy '*' --silent --fail --connect-timeout 5 --max-time 25 \
      --request GET http://supervisor/core/api/states 2>/dev/null |
    jai_filter 'selectedstates' >"$JAI_OUT/live-states.json" 2>/dev/null; then
    echo 'OK: live-states.json from Core GET /states' >>"$JAI_REPORT"
  else
    rm -f "$JAI_OUT/live-states.json"
    echo 'MISSING: live states (Core API denied/unavailable); saved registries remain usable. No permissions changed.' >>"$JAI_REPORT"
  fi
else
  echo 'MISSING: live states (Supervisor token not available or offline mode).' >>"$JAI_REPORT"
fi
if [[ -f "$JAI_OUT/entities.json" ]]; then
  jq '[.[] | select(.platform == "spotify" and (.entity_id | startswith("media_player.")))]' "$JAI_OUT/entities.json" >"$JAI_OUT/spotify-entities.json"
fi
if [[ -f "$JAI_OUT/live-states.json" && -f "$JAI_OUT/spotify-entities.json" ]]; then
  jq --slurpfile entries "$JAI_OUT/spotify-entities.json" \
    '[.[] | select(.entity_id as $id | any($entries[0][]; .entity_id == $id))]' \
    "$JAI_OUT/live-states.json" >"$JAI_OUT/spotify-states.json"
fi

# Version-only local file, not the full HA configuration.
if [[ -f "$JAI_CONFIG/.HA_VERSION" ]]; then
  jq -Rs 'if test("^[0-9]+\\.[0-9]+\\.[0-9]+[a-z0-9.\\r\\n-]*$") then {core_version: rtrimstr("\n")} else {core_version:"unknown"} end' \
    "$JAI_CONFIG/.HA_VERSION" >"$JAI_OUT/core-version.json"
fi
cat >>"$JAI_REPORT" <<'TEXT'
Bubble version: bubble-version.json records HACS metadata, not a guessed version.
An empty/missing record means the installed version needs owner confirmation.
custom-card-files.tsv records local JS presence and hashes; it does not prove loading.
jai-control.json may be absent when the existing panel uses its generated default layout.
No unsaved browser edits are available in local configuration files.
TEXT
(
  cd "$JAI_OUT"
  find . -type f ! -name SHA256SUMS -exec sha256sum {} \; >SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)
COPYFILE_DISABLE=1 tar -czf "$JAI_ARCHIVE" -C "$JAI_DEST" "${JAI_OUT##*/}"
tar -tzf "$JAI_ARCHIVE" >/dev/null
chmod 600 "$JAI_ARCHIVE"
printf '\nEXPORT READY: %s\n' "$JAI_ARCHIVE"
sha256sum "$JAI_ARCHIVE"
printf '\nCollection report:\n'
cat "$JAI_REPORT"
printf '\nShare only the .tar.gz archive shown above. HA configuration was not modified.\n'
