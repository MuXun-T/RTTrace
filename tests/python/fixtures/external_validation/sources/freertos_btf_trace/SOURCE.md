# FreeRTOS-BTF-Trace Source

- Repository: `https://github.com/kuopinghsu/FreeRTOS-BTF-Trace`
- Fixed commit: `791410f5ebb05a9fdf77401228140c60275b5d27`
- Tag: none
- Original paths: `tracedata/example.btf`, `tracedata/example.vcd`, `tracedata/example-4cores.btf`, and `tracedata/example-50k.btf`
- License: MIT; repository root `LICENSE` is copied unchanged and no root NOTICE was found.
- RTOS and format: FreeRTOS; BTF and VCD ASCII trace files.
- Generation: public pre-generated samples. The upstream README describes an included RV64 simulator; this is not a hardware capture.
- Workload: the repository documents FreeRTOS demo workloads including SMP, queue stress, mutex contention, and priority inversion. This documentation is not independent truth.

Acquisition template: clone the repository below `$P7_TMP`, check out the fixed commit, and copy only the four named files and `LICENSE`. No conversion was performed. The repository-level MIT license permits copying subject to preserving the copied license; no file-specific override was observed during audit.

These raw files may be used only as licensed external trace inputs with the recorded provenance. They cannot establish hardware validation, self-acquisition, diagnosis accuracy, generality, proof correctness, or replay pass. No replay was run.
