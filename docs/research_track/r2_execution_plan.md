# R2 Verified Prior-Art Review Execution Plan

## Status And Boundary

Status: executed R2 documentation record, 2026-07-18 (Asia/Shanghai).

Implementation Authorization: R2 Documentation Only

Prior-Art Search: Authorized

Baseline Implementation: Not Authorized

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Automatic Commit: Disabled

The orchestration request specified `gpt-5.6-terra` with high reasoning effort
for this execution role. The available orchestration interface did not expose
model-setting controls or runtime attestation. This records the requested
configuration only; it does not assert that configuration was verified.

Only the five R2 documentation paths are in this execution allowlist. Frozen
R0/R1A/P6/P7 records, code, schemas, fixtures, tests, and the pre-existing
untracked master plan are read only. No B2 source was downloaded, built, run,
adapted, or benchmarked.

## Inputs And Candidate Questions

The review read the master plan, R1A registry/closeout/execution plan, R0
errata/closeout, P6.3 replay boundary, P6/P7 reproducibility indexes, P7.4
replay contract/plan, and the existing closure, sidecar, sidecar-index, and
replay implementation only to map the frozen candidate mechanism. It is fixed
seeds/rules/order; vector budget before reads; dependency closure with a frozen
missing/truncated frontier; sidecar/index access; package identity/checksum/
dependency validation; canonical non-success states; and bounded deterministic
replay/reporting. This is engineering context, not a result.

| Candidate | Search question | Adverse inclusion rule |
| --- | --- | --- |
| C1 | Does prior work select a diagnostic execution/dependency slice under storage, trace, window, or capture limits and preserve an explanation/result? | Include dynamic slicing, online dependency traces, hardware signal slicing, trace minimization, and causal slicing even when not RTOS-specific. |
| C2 | Does prior work construct a dependency/provenance package and bind, validate, or reject incomplete/mismatched inputs for reproducibility/debugging? | Include reproducibility packers and provenance systems even when they do not use RTOS traces. |
| C3 | Does prior work index event/trace data or selectively access it and account for build, storage, cold/warm, reuse, or recall cost? | Include trace tools and generic index/query work; do not assume a latency paper demonstrates net cost. |

## Taxonomy, Queries, And Sources

Query classes were run against Crossref, OpenAlex, arXiv, official DOI/publisher/
proceedings pages, USENIX/ACM/IEEE landing pages where reachable, and official
GitHub repository metadata. Semantic Scholar returned HTTP 429 after the first
request. Google Scholar was not automated or used as evidence. DBLP was in the
source priority but was not needed to resolve the core records after Crossref
and official sources agreed.

| Taxonomy | Exact terms and synonyms | Mechanism/problem terms | Exclusions |
| --- | --- | --- | --- |
| RTOS/embedded trace analysis | `RTOS trace analysis debugging`; `embedded trace analysis`; `Linux kernel trace analysis` | tracepoint, event trace, scheduler, IRQ, post-silicon | medical trace; network packet-only absent diagnostic selection |
| Dynamic/backward/causal slicing | `dynamic backward causal trace slicing`; `dynamic slicing trace analysis debugging`; `causally consistent dynamic slicing` | dependence graph, slicing criterion, backward slice, replay, causality | static-only and unrelated geometric slicing |
| Selective capture/export/query | `selective trace capture debugging dependency`; `on-chip dynamic signal sequence slicing`; `fixed size circular buffer dynamic dependence graph` | window, history, buffer, capture, minimize, export | fixed clipping without dependency/diagnosis relation |
| Dependency/evidence closure | `provenance dependency closure integrity fail closed`; `provenance-aware storage systems`; `reproducible computational package dependencies` | package, dependency, provenance, closure, validation | provenance branding/blockchain without package mechanism |
| Debugging provenance/replay | `record replay provenance scientific workflow`; `reproducible debugging replay` | rerun, unpack, replay, reproducibility | replay without dependency/package mechanism |
| Trace indexing/selective I/O | `trace indexing selective I/O query workload`; `execution trace indexing query analysis`; `Trace Compass trace analysis indexing` | index build, storage, cold, warm, reuse, stream scan | latency-only index claims |
| Identity/integrity/fail-closed | `artifact identity checksum validation fail closed`; `package integrity provenance validation` | SHA-256, identity, mismatch, reject, non-success | integrity without stated invalid-input behavior |

Priority was official ACM/IEEE/USENIX/proceedings or publisher full text,
official code/license, Crossref metadata, OpenAlex metadata/open-access
location, then arXiv. Search snippets, blogs, and unverified secondary
citations were not decisive evidence.

## Citation And Verification Protocol

Backward chaining used Crossref reference lists and the Perera, Garg, Cheney
full text. It identified the foundational dynamic-slicing line and screened
Agrawal and Horgan. Forward chaining was attempted through OpenAlex for four
primary DOIs. OpenAlex returned cited-by counts but rejected the DOI-form query
used to enumerate citers, so no forward-citation result is treated as verified.
That failure is a limitation, not evidence of absence.

`verified` requires official-record and Crossref/OpenAlex agreement on title,
authors, year, venue and DOI, plus inspected primary full text or method
record. `partially verified` has reliable metadata with only an official
abstract/publisher summary/limited method record. `unverified` lacks enough
authoritative metadata or method access. `excluded` is duplicate, out of scope,
or tool-only. Partial/unverified records cannot be the sole basis for a strong
novelty conclusion. Nearest-neighbor selection favors overlap in problem and
runtime representation/mechanism, then safety and empirical evidence.

## B2 And Stop Rules

A B2 is quantitative only with public runnable code/license, faithful
reproduction or audited adapter, documented same raw-input/truth/output/failure
contract, and no material unsupported feature. Failure of a gate yields
`capability-only` or `not comparable`.

Stop for core coverage, a needed R1A change, contradictory sources, inaccessible
decisive method, unfair B2, a need to implement/run, or an unauthorized change.
The main agent intervened after adverse results: supplementary verification and
conclusion narrowing were selected, and a missing material delta must yield
`REFRAME`, not `PROCEED`.

## Module Audit: Execution Plan

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| R2 execution plan and query log | Frozen master/R0/R1A/P6/P7 records; Crossref, OpenAlex, arXiv, official publisher/proceedings/code endpoints | `r2_execution_plan.md` | Terms, source priority, status rules, citation attempt, B2 gates, and stop criteria recorded | Documentation-only; no protected-path edit or execution | OpenAlex forward-citer enumeration failed; Semantic Scholar rate-limited |
