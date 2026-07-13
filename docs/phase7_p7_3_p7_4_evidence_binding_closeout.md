# P7.3/P7.4 Per-Case Evidence Binding Corrective Closeout

P7.5 pre-audit found that the frozen P7.2 raw trace, P7.3 aggregate
package-open report, and P7.4 replay reports lacked per-case package/artifact
closure.  This correction adds four P7.3 manifest/report pairs and four
machine-readable bindings without rewriting an old artifact or changing a
P7.3/P7.4 semantic decision.

The correction commits are `f74d0e8`, `7999f13`, `450b230`, `c852d28`,
`df9d54f`, and `534da29`; the final integration/closeout freeze follows their
independent audit.  Each binding recomputes the sealed P7.2 inventory/raw
facts, P7.3 manifest/source/artifact/package identities, a real P7.3 CLI
reopen report, and the corresponding frozen P7.4 report.  It separately
retains P7.4's legacy aggregate package identity and never equates it with a
per-case P7.3 package identity.

The integration audit freezes the P6.4 hash
`fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`,
P7.2 inventory/raw hashes, the original P7.3 `opened.json` hash
`dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`,
all six P7.4 report hashes, and all P7.4 expected/profile hashes.  It
reproduces each per-case P7.3 report and binding twice with byte equality.

No raw trace is duplicated in the repository.  No source/raw/P7.3/P7.4
canonical input, expected fixture, comparison profile, proof input, or
prohibited path is modified.  The builder has no replay, network, shell,
subprocess, LLM, Advisor, Feedback, or hardware-validation path.  Its P7.3
reopen uses only a temporary package and external temporary report.

Zephyr and Zephelin remain excluded rather than being represented as fake
trace/package closure.  `hardware_validation=false`,
`proof_parity_eligible=false`, `proof_parity=not_evaluated`,
`proof_correctness=not_evaluated`, and `diagnosis_correctness=not_evaluated`.
P7.5 is not started.  P7.6 is not started.
# Final Freeze

The corrective evidence binding is frozen after the independent final review
reported `blocking=0` and `major=0`.
