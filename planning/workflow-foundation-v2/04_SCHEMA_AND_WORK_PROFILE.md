# 4. Strict task semantics and compatibility

## Task work profile

The proposed semantic object is optional as a whole. When present, all four members are required:

```json
{
  "work_profile": {
    "difficulty": "complex",
    "risk": "high",
    "work_type": "implementation",
    "context_depth": "deep"
  }
}
```

Difficulty is the capability needed for reliable execution: trivial, routine, complex, hard or exceptional. Risk is the consequence of error: low, medium, high or critical. Work type is inspection, implementation, testing, review, architecture, integration, performance, research or documentation. Context depth is local, focused, deep or cross_project. This last field is an ordinal/routing hint, not a claim that geography and depth are mathematically one axis; retain compatibility if a later format separates breadth and depth.

Missing means unspecified, not routine. Do not silently infer/write a profile from tags. An inferred recommendation must carry its heuristic basis. A short edit can be critical-risk; a long repository search can be routine-difficulty/deep-context. Neither axis changes priority or claim authorization.

Do not store model, reasoning_effort, agent_profile or provider names inside work_profile. Actual dispatch attempts may record a requested role, resolved external policy, observed model/effort and usage evidence. These are execution facts, not permanent task requirements.

## Strictness and versioning

New-format semantic records reject unknown keys and wrong types, including booleans masquerading as integer revisions. Intentional extensibility is namespaced and bounded. Introduce the next native writer version explicitly; keep reader support and a documented warning policy for old v2/v3 plans. Plan wire version, DB migration version and work-profile schema version are distinct contracts.

Legacy plan omission must not clear an existing profile. Define explicit new-format clear/update operations. Profiles, provenance and relevant context changes participate in semantic revisions and fingerprints. No-op application should not churn task versions merely because an identical file was applied again. Active claim profile changes require deliberate refresh/requalification or handoff, not silent mutation of the worker's assumptions.

## Bootstrap without circular dependency

The delivered native apply plans remain schema 3. `machine/work_profiles.json` and rich task sheets carry reviewed profiles but are not currently authoritative runtime fields. Native support is implemented and tested first. S05 promotes these exact sidecars through the new supported transaction interface on disposable fixtures, then through a separately reviewed administrative operation when the upgraded live runtime is ready. Do not reapply the original bootstrap to overwrite running tasks.

`schemas/work-profile-v1.schema.json` is a proposed contract and an offline check, not evidence of installed support. The exact profile sidecar is the migration oracle. The strict pre-ledger compiler must preserve its declared run/lane format; the existing generic schema-2 compiler is never used on this package.
