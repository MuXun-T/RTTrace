#!/usr/bin/env bash
set -euo pipefail

usage() { echo "usage: $0 --metadata flash_metadata.json [--gate-ready-file gate.ready] [--dry-run|--emit-persistent-command] firmware.elf" >&2; exit 64; }
metadata=""; mode=""; gate_ready_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --metadata) metadata=${2:-}; shift 2 ;;
    --gate-ready-file) gate_ready_file=${2:-}; shift 2 ;;
    --dry-run|--emit-persistent-command) [[ -z "$mode" ]] || usage; mode=$1; shift ;;
    -*) usage ;;
    *) image=$1; shift; [[ $# -eq 0 ]] || usage ;;
  esac
done
[[ -n "${image:-}" && -n "$metadata" && -n "$mode" && -s "$image" && -s "$metadata" ]] || usage
expected=$(python3 - "$metadata" "$image" <<'PY'
import hashlib, json, pathlib, sys
m=json.load(open(sys.argv[1], encoding='utf-8')); image=pathlib.Path(sys.argv[2]); firmware=m.get('firmware', m); gate=firmware.get('capture_gate', {})
variant=firmware.get('variant', m.get('firmware_variant')); symbol=gate.get('symbol', m.get('capture_gate_symbol')); address=gate.get('address', m.get('capture_gate_address')); elf_sha256=firmware.get('elf_sha256', m.get('elf_sha256'))
addresses={'H3_COLLECTOR_SMOKE':'0x20000000'}
if variant not in {'GPIO_UART_RECORDER', 'TASK_SMOKE', 'MUTEX_SMOKE', 'IRQ_SMOKE', 'COMBINED_SMOKE', 'H3_COLLECTOR_SMOKE'} or symbol != 'capture_armed' or address != addresses.get(variant, '0x20000004'): raise SystemExit('capture-gate metadata mismatch')
if m.get('target',{}).get('target') != 'stm32f103ze' or m.get('target',{}).get('probe_uid') != '0001A0000001': raise SystemExit('target binding mismatch')
if elf_sha256 != hashlib.sha256(image.read_bytes()).hexdigest(): raise SystemExit('ELF hash mismatch')
print(address)
PY
)
symbol=$(arm-none-eabi-nm -an "$image" | awk '$3 == "capture_armed" { print "0x" $1 }')
[[ "$symbol" == "$expected" ]] || { echo "capture-gate symbol address mismatch" >&2; exit 2; }
echo "preflight target=stm32f103ze uid=0001A0000001 symbol=capture_armed address=$expected"
[[ "$mode" == --dry-run ]] && exit 0
[[ -n "$gate_ready_file" && -s "$gate_ready_file" ]] || { echo "emit requires a non-empty --gate-ready-file created after matching RTD1 BOOT" >&2; exit 2; }
python3 - "$metadata" "$image" "$gate_ready_file" <<'PY'
import configparser, hashlib, json, pathlib, sys
m=json.load(open(sys.argv[1], encoding='utf-8')); firmware=m.get('firmware', m); gate=firmware.get('capture_gate', {}); ready=pathlib.Path(sys.argv[3]).read_text(encoding='ascii')
values={}
for line in ready.splitlines():
    if '=' in line:
        key, value=line.split('=', 1); values[key]=value
expected={
    'schema_version':'phase1-gate-ready-v1', 'boot_observed':'1',
    'variant':firmware.get('variant', m.get('firmware_variant')),
    'build_hash':firmware.get('build_hash', m.get('build_hash')),
    'session':firmware.get('session_id', m.get('session_id')),
    'elf_sha256':hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest(),
    'capture_gate_symbol':gate.get('symbol', m.get('capture_gate_symbol')), 'capture_gate_address':gate.get('address', m.get('capture_gate_address')),
}
if any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit('gate-ready file does not match the selected booted firmware')
allowed_policies = {None, 'preserve-0x00000002'}
if 'firmware' in m:
    allowed_policies.add('explicit-mask-0x00000002')
if values.get('modem_policy') not in allowed_policies:
    raise SystemExit('gate requires a retained or explicitly recorded CH340 modem policy')
PY
echo "persistent_session_command=write32 $expected 0x00000001"
echo "Enter that command only in the already-attached UID-pinned SWD session. This script never opens or disconnects a debugger."
