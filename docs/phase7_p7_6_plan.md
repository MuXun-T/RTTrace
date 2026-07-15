# P7.6 Plan

The approved implementation is split into five bounded items:

1. Contract freeze: closed model, units, null semantics, environment identity,
   acquisition fail-closed rule and claim boundary.
2. Harness/raw capture: direct use of existing package materialization,
   package reopen, P7.4 replay and P7.5 validation APIs; all output is outside
   the repository.
3. Repeat/warm/scale matrix: four frozen FreeRTOS cases, cold/warm labels,
   one warm-up and five measured runs, full failure retention and MAD summary.
4. Canonical evidence: deterministic JSON and SHA-256 over the independent
   benchmark evidence only; no timestamp, PID, hostname, absolute path or
   proof hash.
5. Closeout: focused and adjacent regression, full Python regression,
   no-mutation and claim-boundary audit.

Only new P7.6 files are allowed. Existing parser, export, collector, schemas,
fixtures and reports are read-only dependencies.
