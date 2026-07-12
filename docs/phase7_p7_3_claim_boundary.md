# P7.3 package-open claim boundary

P7.3 supports deterministic, offline facts about a declared directory package:
manifest conformance, frozen P7.2 provenance, byte/checksum checks, mutation
counts, package-open classification, and canonical reports. It supports no
hardware validation; every accepted report fixes `hardware_validation=false`
and `replay_evaluated=false`.

The reports are package-open records only. They do not support semantic replay,
trace reconstruction, diagnosis or root-cause correctness, proof parity or
correctness, accuracy claims, hardware claims, or `replay_pass`/`replay_fail`.
P7.4 is the separately planned prerequisite for deterministic semantic replay.
