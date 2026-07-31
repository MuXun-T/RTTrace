#!/usr/bin/env bash
set -euo pipefail

usage() { echo "usage: $0 --metadata capture.json [--dry-run|--confirm] firmware.elf" >&2; exit 64; }
metadata=""; mode=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --metadata) metadata=${2:-}; shift 2 ;;
    --dry-run|--confirm) [[ -z "$mode" ]] || usage; mode=$1; shift ;;
    -*) usage ;;
    *) image=$1; shift; [[ $# -eq 0 ]] || usage ;;
  esac
done
[[ -n "${image:-}" && -n "$metadata" && -n "$mode" ]] || usage
[[ -s "$image" && -s "$metadata" ]] || { echo "ELF or metadata missing/empty" >&2; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
expected=$(python3 - "$metadata" "$image" "$script_dir/../pinmaps/phase1_board_contracts.json" <<'PY'
import hashlib, json, pathlib, sys
m=json.load(open(sys.argv[1], encoding="utf-8")); image=pathlib.Path(sys.argv[2]); contracts_path=pathlib.Path(sys.argv[3]); contracts=json.load(open(contracts_path, encoding="utf-8"))
profiles=contracts.get("profiles", {})
contract=profiles.get(m.get("board_id"))
if contracts.get("schema_version") != "phase1-board-contracts-v1" or not isinstance(contract, dict):
    raise SystemExit("metadata board_id does not match a frozen Phase 1 board")
pinmap_name=contract.get("pinmap_file")
pinmap=(contracts_path.parent / pinmap_name).resolve() if isinstance(pinmap_name, str) else None
if pinmap is None or pinmap.parent != contracts_path.parent.resolve() or not pinmap.is_file():
    raise SystemExit("board contract pinmap is invalid")
pinmap_data=json.load(open(pinmap, encoding="utf-8"))
if pinmap_data.get("board_id", m["board_id"]) != m["board_id"]:
    raise SystemExit("board contract pinmap does not match metadata board ID")
if m.get("pin_map_version") != "phase1-pinmap-v2" or m.get("firmware_variant") not in {"BASE","GPIO_ONLY","GPIO_UART","GPIO_UART_RECORDER","TASK_SMOKE","MUTEX_SMOKE","IRQ_SMOKE","COMBINED_SMOKE","H3_COLLECTOR_SMOKE"}:
    raise SystemExit("metadata rejects retired pin map or firmware variant")
if m.get("pin_map_version") != contract.get("pin_map_version") or pinmap_data.get("schema_version") != contract.get("pin_map_version"):
    raise SystemExit("metadata pin-map version does not match frozen board contract")
for field in ("session_id", "firmware_sha256", "build_config_sha256", "pinmap_sha256"):
    value=m.get(field)
    if not isinstance(value, str) or not value or value.startswith("replace-with-"):
        raise SystemExit(f"metadata missing required {field}")
target=m.get("target", {})
expected_target=contract.get("target", {})
if target.get("target") != expected_target.get("target") or target.get("probe_uid") != expected_target.get("probe_uid"):
    raise SystemExit("metadata target or probe UID does not match the pinned Phase 1 board")
if "mcu_uid_words" in expected_target and target.get("mcu_uid_words") != expected_target["mcu_uid_words"]:
    raise SystemExit("metadata MCU UID does not match the frozen board contract")
if m["pinmap_sha256"] != hashlib.sha256(pinmap.read_bytes()).hexdigest():
    raise SystemExit("metadata pin-map hash does not match phase1-pinmap-v2")
actual=hashlib.sha256(image.read_bytes()).hexdigest()
if m.get("elf_sha256") != actual:
    raise SystemExit("metadata ELF hash does not match selected image")
print(m["board_id"] + " " + actual)
PY
)
board=${expected%% *}; elf_sha256=${expected#* }
echo "preflight board=$board target=stm32f103ze uid=0001A0000001 elf_sha256=$elf_sha256 variant=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["firmware_variant"])' "$metadata")"
if [[ "$mode" == "--dry-run" ]]; then
  echo "dry-run passed: no probe connection, erase, flash, reset, or target access occurred"
  exit 0
fi
read -r -p "Program verified ELF with sector erase through UID 0001A0000001? [y/N] " answer
[[ "$answer" == y || "$answer" == Y ]] || { echo "cancelled" >&2; exit 3; }
"$script_dir/target_check.sh"
log_dir="${RTD_PHASE1_FLASH_LOG_DIR:-$(dirname -- "$image")/phase1_flash_logs}"; mkdir -p "$log_dir"
log="$log_dir/$(basename -- "$image").$(date -u +%Y%m%dT%H%M%SZ).log"
{ pyocd flash --target stm32f103ze --uid 0001A0000001 --erase sector --format elf "$image"; pyocd commander --target stm32f103ze --uid 0001A0000001 --connect halt --command status; pyocd commander --target stm32f103ze --uid 0001A0000001 --connect attach --command 'reset hardware' --command exit; } 2>&1 | tee "$log"
echo "retained flash log: $log"
