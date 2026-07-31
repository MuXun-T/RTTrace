#!/usr/bin/env bash
set -euo pipefail

backup="/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/evidence/firmware_backup/pre_phase1_keil_firmware_20260725.bin"
expected="222444b4bd6c551822acd673b3f2325c3c86bb0c280ea96afad98479ee4ecca4"
[[ ${1:-} == "--dry-run" || ${1:-} == "--confirm" ]] || { echo "usage: $0 --dry-run|--confirm" >&2; exit 64; }
[[ -f "$backup" && $(stat -c %s "$backup") == 524288 ]] || { echo "backup missing or not 512 KiB" >&2; exit 2; }
actual=$(sha256sum "$backup" | awk '{print $1}')
[[ "$actual" == "$expected" ]] || { echo "backup SHA-256 mismatch" >&2; exit 2; }
echo "backup verified: $backup sha256=$actual target=stm32f103ze uid=0001A0000001 address=0x08000000"
if [[ $1 == "--dry-run" ]]; then echo "dry-run passed: no probe connection, option-byte write, reset, or restore occurred"; exit 0; fi
read -r -p "Restore this user-owned Keil image at 0x08000000? [y/N] " answer
[[ "$answer" == y || "$answer" == Y ]] || { echo "cancelled" >&2; exit 3; }
pyocd flash --target stm32f103ze --uid 0001A0000001 --erase sector --base-address 0x08000000 "$backup"
pyocd commander --target stm32f103ze --uid 0001A0000001 --connect halt --command status
echo "restore completed; option bytes were not touched"
