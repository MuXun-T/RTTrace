# Phase 2 Migration and Retention

The published initial version is `rtd-phase2-v1.0`. Consumers reject a different
major version and a future minor version. A future minor release may add only
optional information that does not change identity, integrity, truth or
authority semantics. A major release requires a separate reader and explicit
owner migration approval.

Published JSON, JSONL, session seals and raw artifacts are never overwritten.
Corrections create a new contract object with its own digest and a
`prior_*_ref` containing the corrected record's digest. A version collection
must retain its complete, linear prior chain. The unique current/latest version
is the sole unreferenced leaf; forks, cycles, dangling references, disconnected
history, and multiple leaves are rejected. A sealed ledger session is not
migrated or appended; work must start a new session. There is intentionally no
overwrite or delete CLI.

Raw, partial, invalid and non-manifested captures have the same retention
obligation. A raw artifact inventory uses a relative logical name, availability
state, hash and retention class; it never stores a workstation absolute path.
Redaction or derivative conversion creates a new artifact record and cannot
replace an original raw artifact.

The structural validator retains invalid and pending captures with the same
history rule. Eligibility for a score is a separate, stricter admission check;
it does not erase, relabel, or rewrite retained material.
