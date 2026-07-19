# R2 B2 Candidate Registry

## Status And Gate

This is assessment only. No candidate was downloaded, installed, built, run,
adapted, benchmarked, or compared. A public repository/license is necessary
but not sufficient: B2 also needs faithful reproduction or adapter and the same
raw-input, truth, output, failure-policy, and metric contract.

| Candidate | Source and proximity | Code/license evidence | Faithful/same-contract assessment | Classification | Consequence |
| --- | --- | --- | --- | --- | --- |
| B2-1 ReproZip | P5/P6; dependency capture, package creation, unpack/reproduction; C2-related, not C1 closure | [VIDA-NYU/reprozip](https://github.com/VIDA-NYU/reprozip), branch `1.x`; GitHub API: BSD-3-Clause | Code/license yes; no reviewed mapping from RTOS raw trace plus independent truth to R1A closure/frontier/status/reason-code contract; no attempt. | `not comparable` | Literature context only; no ranking, accuracy, cost, or superiority comparison. |
| B2-2 Trace Compass | Trace-analysis platform, not a verified closure/package-validity method | [eclipse-tracecompass/org.eclipse.tracecompass](https://github.com/eclipse-tracecompass/org.eclipse.tracecompass); GitHub API: EPL-2.0 | Code/license yes; no reviewed mapping for R1A raw trace, truth, frontier, identity, reason codes, or net-cost contract; no attempt. | `capability-only` | Tool/capability context only, not quantitative B2. |
| B2-3 Online tracing/dynamic slicing | P2; closest C1 bounded-dependency history and slicing | No runnable repository/license verified | No faithful source/reproduction route or same package contract. | `not comparable` | Retain as nearest literature; do not substitute a weaker self-made slice baseline. |
| B2-4 Causally Consistent Dynamic Slicing | P3; causal/backward/forward slicing theory | No runnable artifact/license verified; proceedings CC-BY is not software-license evidence | Formal pi-calculus model has no audited RTOS trace package/truth/output adapter. | `not comparable` | Literature matrix only. |

| Required gate | B2-1 | B2-2 | B2-3 | B2-4 |
| --- | ---: | ---: | ---: | ---: |
| Public runnable code and software license | yes | yes | no | no |
| Faithful reproduction demonstrated | no attempt | no attempt | no route | no route |
| Raw input, truth, output, failure, metrics mapping | no | no | no | no |
| Quantitative eligible | no | no | no | no |

No B2 is `quantitative eligible`. That does not imply an external method is
weak. Any later claim must follow the master-plan downgrade branch or return to
R1A/R2; this phase cannot implement a remedy.

## Module Audit: B2 Registry

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| B2 code/license and contract assessment | P2/P3/P5/P6; official GitHub metadata for ReproZip/Trace Compass; R1A gate | `r2_b2_candidate_registry.md` | Two code licenses verified; all candidates fail a required same-contract gate | No clone/build/run/adapter/benchmark | No quantitative B2; no authorized remedy in R2 |
