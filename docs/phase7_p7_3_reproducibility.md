# P7.3 reproducibility

Run `python3 tool/run_external_package_reopen.py --package DIR --output REPORT`
twice per package. Reports use canonical UTF-8 JSON, sorted keys and sorted
reason/limitation collections; they contain no timestamp, local absolute path,
username, environment data, or replay result. The canonical CLI chain covers
`opened`, `opened_reference_only`, `blocked`, `invalid`, and `unsupported` with
exit codes 0, 2, 4, 3, and 5 respectively.

The focused suite creates package copies in temporary directories from frozen
P7.2 sources. It checks byte equality and SHA-256 equality for two runs of each
canonical result. The stored `packages/reports/opened.json` is a small canonical
example; no P7.2 trace byte is duplicated in this directory.
Its SHA-256 is `dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`.
