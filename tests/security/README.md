# Security validation suite (Phase 8)

One automated negative test per row of the "Cross-Tenant & Privilege-Escalation
Scenarios" table in [docs/03-threat-model.md](../../docs/03-threat-model.md),
which designates them **mandatory test cases** for this phase.

Every test class here is named `TestScenarioNN_...` so coverage is auditable by
reading the file listing against the threat-model table — if a scenario number
is missing, that defense is unproven.

| # | Scenario | Where proven |
|---|---|---|
| 1 | Tenant hint tampering | `test_scenarios.py::TestScenario01TenantHintTampering` |
| 2 | Cross-tenant IDOR, 404 not 403 | `test_scenarios.py::TestScenario02CrossTenantIdor` |
| 3 | Tenant role attaching a platform permission | `test_scenarios.py::TestScenario03PlatformPermissionInTenantRole` |
| 4 | Self-escalation via role assignment | `test_scenarios.py::TestScenario04SelfEscalation` |
| 5 | Suspended membership, still-valid JWT | `test_scenarios.py::TestScenario05SuspendedMembershipMidSession` |
| 6 | Refresh-token reuse after rotation | `test_scenarios.py::TestScenario06RefreshTokenReuse` |
| 7 | Vector query without a tenant filter | `test_scenarios.py::TestScenario07VectorNamespaceIsolation` |
| 8 | Worker tenant-context bleed | `test_scenarios.py::TestScenario08WorkerContextBleed` |
| 9 | Impersonation escalated beyond support scope | `test_scenarios.py::TestScenario09ImpersonationScope` |
| 10 | Permission requiring an unentitled feature | `test_scenarios.py::TestScenario10FeatureEntitlement` |
| 11 | Account hijack by email collision | `test_scenarios.py::TestScenario11OAuthEmailHijack` |
| 12 | Pooled connection retaining RLS context | `test_scenarios.py::TestScenario12PooledConnectionContext` |

Plus `test_append_only_audit.py`, which guards the STRIDE table's repudiation
mitigation (append-only audit tables) — not a numbered scenario, but a
DB-grant invariant that regressed once already and would regress silently.

Tests marked `integration` need the dev Postgres running; see
[tests/integration/README.md](../integration/README.md).
