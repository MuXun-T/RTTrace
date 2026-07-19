# R2 Verified Prior-Art Review

## Scope And Screening

This is an R2 prior-art record for frozen R1A candidates, not a final claim
freeze, experiment, or baseline result. Fifteen retained screened records:
2 `verified`, 7 `partially verified`, 2 `unverified`, 4 `excluded`. The core
matrix has seven non-duplicate scholarly records. The remaining two partial,
two unverified, and four excluded leads were incomplete, duplicate, out of
scope, or tool-only. These are ledger counts, not all work.

## Core Paper Records

| ID/status | Title; authors; year; venue; DOI/official URL; metadata and method source | Problem; representation; mechanism | Safety; evidence | Code; license | C1/C2/C3 relevance |
| --- | --- | --- | --- | --- | --- |
| P1 partial | *Dynamic program slicing*; Hiralal Agrawal, Joseph R. Horgan; 1990; ACM SIGPLAN Notices; [10.1145/93548.93576](https://doi.org/10.1145/93548.93576). Metadata: Crossref. Method: publisher access restricted; metadata only. | Debugging; execution/dependence slice; dynamic slicing. | Full method/experiment not inspected. | No repository/license verified. | C1 foundational adverse neighbor, not decisive alone. |
| P2 partial | *A system for debugging via online tracing and dynamic slicing*; Vijay Nagarajan, Dennis Jeffrey, Rajiv Gupta, Neelam Gupta; 2011; Software: Practice and Experience; [10.1002/spe.1105](https://doi.org/10.1002/spe.1105). Metadata: Crossref/OpenAlex. Method: publisher-deposited Crossref abstract; PDF not inspected. | Failed-run debugging; in-memory dynamic dependence graph; fixed-size circular buffer, selective dependency storage, on-demand slice. | Abstract reports trace rate 16 to 0.8 bytes/instruction, 16-MB/20M-instruction history, around-one-second slicing, and factor-19 tracing slowdown. No package-validity contract. | No repository/license verified. | Closest operational C1 neighbor. |
| P3 verified | *Causally Consistent Dynamic Slicing*; Roly Perera, Deepak Garg, James Cheney; 2016; CONCUR 2016, LIPIcs 59, Article 18; [10.4230/LIPIcs.CONCUR.2016.18](https://doi.org/10.4230/LIPIcs.CONCUR.2016.18). Metadata: official Dagstuhl/Crossref/OpenAlex. Method: official proceedings PDF inspected. | Concurrent dynamic slicing; pi-calculus execution/configuration; causal equivalence, backward/forward slices, Galois connection. | Formal result, Agda formalisation, scheduler example; paper says slicing does not automatically isolate bugs. | Artifact/code license not verified; proceedings CC-BY. | Adverse C1/replay neighbor; no C2 fail-closed or C3 ledger. |
| P4 partial | *On-chip dynamic signal sequence slicing for efficient post-silicon debugging*; Yeonbok Lee, Takeshi Matsumoto, Masahiro Fujita; 2011; ASP-DAC 2011, pp. 719-724; [10.1109/ASPDAC.2011.5722280](https://doi.org/10.1109/ASPDAC.2011.5722280). Metadata: IEEE via Crossref/OpenAlex. Method: metadata/abstract only. | Post-silicon debugging; on-chip dynamic signal sequences; dynamic signal slicing. | Full safety/evaluation not inspected. | No repository/license verified. | Adverse embedded C1 neighbor. |
| P5 verified | *ReproZip: The Reproducibility Packer*; Remi Rampin, Fernando Chirigati, Dennis Shasha, Juliana Freire, Vicky Steeves; 2016; JOSS 1(8):107; [10.21105/joss.00107](https://doi.org/10.21105/joss.00107). Metadata: JOSS/Crossref/OpenAlex. Method: official JOSS PDF inspected. | Reproducible experiments; files, binaries, dependencies, configuration in `.rpz`; system-call-traced packing and unpack modes. | Documentation/examples support; no RTOS diagnosis or invalid-package false-accept experiment. | [VIDA-NYU/reprozip](https://github.com/VIDA-NYU/reprozip); BSD-3-Clause from GitHub API. | Adverse C2 package/reopen neighbor, not C1 closure/C3 index. |
| P6 partial | *ReproZip*; Fernando Chirigati, Remi Rampin, Dennis Shasha, Juliana Freire; 2016; SIGMOD 2016, pp. 2085-2088; [10.1145/2882903.2899401](https://doi.org/10.1145/2882903.2899401). Metadata: ACM/Crossref/OpenAlex. Method: P5 identifies it; ACM full text not inspected. | ReproZip package reproduction. | No separate inference beyond P5. | Same project; BSD-3-Clause metadata as P5. | C2 corroborating venue record, not independent neighbor. |
| P7 partial | *CDE: A Tool for Creating Portable Experimental Software Packages*; Philip Guo; 2012; Computing in Science & Engineering; [10.1109/MCSE.2012.36](https://doi.org/10.1109/MCSE.2012.36). Metadata: IEEE/Crossref/OpenAlex. Method: metadata only. | Portable software experiment package by system-call interposition. | No inspected safety/evaluation detail. | No repository/license verified. | C2 adverse lead, insufficient for strong coverage. |

## Adverse Findings

For C1, P2 is the closest operational neighbor: online dependency tracking,
bounded retained history, selective dependency retention, and on-demand
slicing. P3 supplies causal backward/forward slicing and P4 applies sequence
slicing to embedded debug. A broad dependency-aware trace-selection candidate
is therefore occupied. R1A additionally states vector budget-before-read,
frozen frontier, local sidecar, package identity validation, and net-cost
ledger. This review does not prove those fields absent or materially
non-obvious; P2's bounded history is close. C1 cannot remain unchanged.

For C2, P5 verifies established dependency capture, package construction, and
reopen. Its inspected text does not state all R1A source/package binding or
canonical mismatch rejection. P6/P7 broaden the package concern, but missing
method access blocks stronger coverage claims. The remaining fail-closed
distinction needs a new narrow R1A candidate and integrity/provenance R2.

For C3, trace-tool and generic index/query leads were found but no accessible
same-contract full text verified build/storage/cold/warm accounting. Missing
coverage is not evidence of a material delta. C3 has no verified delta.

## Bias Controls And Limitations

P4 was included despite hardware-debug scope and P5/P7 despite package scope.
No diagnosis, semantic preservation, security, or performance conclusion is
inferred from an abstract. Semantic Scholar was rate-limited and OpenAlex
forward-citer enumeration failed; missing results are not evidence of absence.

## Module Audit: Verified Review

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| C1/C2/C3 core review | Official Dagstuhl/JOSS PDFs; Crossref, OpenAlex, arXiv; DOI/publisher pages; GitHub metadata | `r2_verified_prior_art_review.md` | 2 full-method verified records; 7 partial; adverse C1/C2 overlap retained; C3 unresolved | No code/download/build in repository, no baseline or experiment | C1 material delta not demonstrated; integrity/index full-text coverage incomplete |
