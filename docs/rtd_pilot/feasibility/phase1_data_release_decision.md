# Phase 1 data-release decision

## Owner decision

```text
project_owner = PROJECT_OWNER
technical_implementer = PROJECT_OWNER
reviewer = SELF_REVIEW
approver = PROJECT_OWNER
approval_authority = OWNER_APPROVAL
approval_status = OWNER_APPROVED
decision_date = 2026-07-30
data_release_route = OWNER_CONTROLLED_RESEARCH_ACCESS
data_release_rights = PASS
```

The project owner controls the self-generated Phase 1 code, experiment
records, captures, and derived results. This route permits controlled access
for thesis verification without requiring immediate public release. It is a
lawful and executable H3 controlled-access route for this single-owner project.

## Access classification

| Material class | Default access | Permitted audience | Delivery rule |
| --- | --- | --- | --- |
| Raw DSView `.dsl`, complete CSV, UART raw log, gate receipt, flash/build record, complete ELF, and locally identifying build artifact | Owner-controlled | `PROJECT_OWNER`, `OWNER_APPROVED_REVIEWER`, `THESIS_REVIEWER_WHEN_NEEDED` | Local inspection, encrypted archive, offline storage, or supervised viewing chosen by the project owner. |
| Redacted screenshot, derived validation JSON, marker map, build instructions, tool versions, file hashes, test summary, review report, and access statement | Owner may publish or attach to thesis | Audience selected by the project owner | Publication is optional and is not a P1 freeze condition. |
| Third-party RTOS source, vendor BSP/library, tool binary, restricted example, or third-party-owned firmware/image | No redistribution unless its license separately permits it | Access by official source or reproducible acquisition route | Record origin, official source, version/commit, license, SHA-256, project patch, and reproduction method. |

Not redistributing a third-party component is compatible with
`data_release_rights = PASS`: the project supplies the lawful reproduction and
access route rather than copying material it does not own.

## Redaction and preservation

Before an owner-approved external copy is made, remove or replace nonessential
host absolute paths, usernames, hostnames, device or USB serial numbers,
CMSIS-DAP UID, logic-analyzer serial number, unrelated directories, temporary
paths, personal information, keys, tokens, and credentials. Redaction creates
a new derivative and never changes the retained original.

```text
raw_artifacts_are_immutable = true
raw_artifacts_stored_outside_git_when_necessary = true
silent_rewrite_prohibited = true
derived_artifacts_must_not_replace_raw = true
hash_algorithm = SHA-256
```

The machine-readable raw-artifact inventory is
`phase1_data_release_inventory.json`. It deliberately uses storage classes and
filenames instead of public absolute storage paths.

## Scope and limits

This decision controls access to Phase 1 evidence only. It does not publish
raw files automatically, alter a third-party license, create fault truth,
create a Case, or assert any Phase 2 implementation or experimental result.
