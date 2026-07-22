# RTD-Pilot Phase 0 Data License and Release Checklist

No license clearance is asserted by this document. Before any data collection,
the designated release owner must complete the following per asset, retaining
license text, notice, origin, rights holder, modification status, redistribution
decision, and reviewer sign-off.

| Asset | Required public/release posture | Permitted fallback | Permanent route blocker |
| --- | --- | --- | --- |
| RTOS license | Publish applicable license/NOTICE and exact version, or document lawful non-redistributable dependency. | Build/obtain instructions only if redistribution is forbidden but reproducible access is lawful. | No lawful use or no publication disclosure route. |
| BSP | Publish owned modifications and notices; identify vendor binary/blob terms. | Versioned acquisition script/instructions. | BSP terms prevent lawful study/reproduction and cannot be disclosed. |
| toolchain | Publish version, flags, license, and build procedure. | Script to obtain proprietary tool where permitted. | Tool cannot legally be described/reproduced or its restriction invalidates results. |
| logic analyzer software | Publish configuration/export format and license/notice. | Controlled configuration export or manual protocol if license forbids redistribution. | Raw Observer evidence cannot be inspected or independently assessed. |
| board vendor files | Publish required notices and source/config legally redistributable. | Vendor download instructions plus hashes/version IDs. | Board terms prohibit the required reproducibility/observer setup. |
| firmware source | Public release with notices and build instructions for all owned code. | Controlled access only for a documented third-party-restricted component. | Core firmware/config cannot be reviewed enough to establish Case identity. |
| raw Trace | Public raw files with Injection Ledger, OAR, CVR, CCM, and CIR linkage where lawful and privacy-safe. | Controlled or reproducibly requestable access with checksum/inventory and independent review route. | Neither public nor controlled/reviewable access is lawful. |
| Observer records | Public enough to inspect OAR predicates, manifestation/adjudication boundary, alignment, and observer status where lawful. | Controlled access with redacted public metadata and audit procedure. | OAR manifestation truth cannot be independently inspected. |
| derived data | Public tables/derived intervals, Evidence Lineage, Diagnostic Relevance Audit, Diagnosis Verdict, and CCM/CIR references needed to regenerate claims. | Controlled raw access only if public derived analysis remains auditable. | Final claims cannot be regenerated/audited. |
| publication tables | Public, regenerable scripts and input manifest. | None for claim-bearing final tables. | Tables cannot be regenerated from disclosed/controlled evidence. |
| anonymous review package | Rights-cleared minimal source, protocol, data-access statement, and blinded artifacts. | Secure reviewer access process. | Review package would violate rights or conceal claim-bearing data. |
| public release | Repository/archive with licenses, notices, data classification, and omissions. | Controlled-access archive with durable request policy. | No durable lawful release/access path. |

P7's existing rule remains informative but insufficient for new hardware data:
licensed external traces require a verified LICENSE/NOTICE, fixed commit and
explicit redistribution/ground-truth status
([P7 plan](../../phase7_p7_2_licensed_external_trace_plan.md)). The bundled
uC/OS tree carries Apache-2.0 text, including redistribution notice obligations
([uC/OS license](../../../RTOS/uC-OS3-develop/LICENSE)), but this does not
clear a selected board, BSP, analyzer, firmware, or newly captured data.

The release owner must recheck this entire matrix for the exact selected
board/RTOS/BSP/toolchain/analyzer/firmware configuration at H3 and again before
collection and submission. Phase 0 asserts neither an existing clearance nor a
data-release route.
