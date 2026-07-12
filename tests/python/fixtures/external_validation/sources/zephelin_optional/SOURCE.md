# Zephelin Source

- Repository: `https://github.com/antmicro/zephelin`
- Fixed commit: `ca37e2efea312f39a8670078daa1a14a2361e358`
- Tag: none
- Audited paths: `README.md` and `scripts/run_renode.py`
- License: Apache-2.0; repository root `LICENSE` is copied unchanged and no root NOTICE was found.
- RTOS and format: Zephyr; CTF output with documented conversion to TEF.
- Generation environment: Renode.
- Workload: upstream gesture-recognition demo, not executed in P7.2.

Acquisition template: clone below `$P7_TMP`, check out the fixed commit, then follow the upstream Renode generation and CTF-to-TEF conversion instructions only after separately approving the toolchain and generated artifact. No trace was copied or converted in this phase.

This is an externally generated quasi-real source audit only. It is not hardware validation, a self-acquired trace, an AI-performance result, independent diagnosis truth, replay result, or replay pass.
