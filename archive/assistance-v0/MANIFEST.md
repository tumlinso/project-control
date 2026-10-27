# Assistance v0 archive manifest

These files are preserved from Project Control checkpoint `c14ad76` while the
deterministic MCP server is separated from local model assistance. The archive
is outside `src/project_control`, wheel package discovery, and pytest's active
`tests/` tree. Do not treat archived commands or contracts as current product
interfaces.

## Source and destinations

| Original path at `c14ad76` | Preserved path | Intended destination or note |
|---|---|---|
| `src/project_control/as1_jobs.py` | `as1_jobs.py` | Inquiry broker; archived, no active replacement in Project Control. |
| `src/project_control/as1_skill.py` | `as1_skill.py` | Model-mediated skill selection and assembly; deterministic knowledge retrieval moves to `project-source` and Project Control search/context. |
| `src/project_control/as1_surface.py` | `as1_surface_v0.py` | Old MCP composition; replaced by active `src/project_control/as1_surface.py`. |
| `src/project_control/observer_analysis.py` | `observer_analysis.py` | Inference supervisor/backend; outside Project Control. |
| `src/project_control/runtime_binding.py` | `runtime_binding.py` | Local model-agent runtime binding; outside Project Control. |
| `src/project_control/local_runtime/**` | `local_runtime/**` | Local investigator, execution frames, lifecycle and deployment material; archived pending a separate owner. |
| `src/project_control/services/local_investigate.py` | `services/local_investigate.py` | Local investigator; archived. |
| `src/project_control/assistance/**` | `assistance/**` | Inquiry, attention, broker, LAB experiment/session logic, execution adapters, and associated state models. LAB logic is being extracted to the independent Project-Lab repository; other orchestration has no Project Control replacement. |
| `src/project_control/skills.py` | `skills.py` | Skill registry; source content becomes ordinary indexed, versioned knowledge. |
| `src/project_control/skill_context.py` | `skill_context.py` | Model-mediated skill context assembly; archived. |
| `src/project_control/skill_ingestion.py` | `skill_ingestion.py` | Skill-specific archive ingestion; future guidance formats belong in source knowledge adapters. |
| `src/project_control/adapters/local_worker.py` | `adapters/local_worker.py` | Local model worker status adapter; no longer part of readiness or retrieval. |
| `src/project_control/cli.py` | `cli_v0.py` | Former CLI including inquiry/LAB commands; active CLI is a small profile launcher and project registry. |
| `src/project_control/app.py` | `app_v0.py` | Former application assembly with inference, inquiry and investigator lifecycle; active application is model-free. |
| `tests/as1/test_pc_as1_{context,inquiry_cache,inquiry_surface,jobs,skill,surface}.py` | `tests/as1/` | Tests for retired response-detail and inquiry APIs. Replaced with current source/profile boundary tests. |
| `tests/test_{local_investigate,observer_analysis_registry,observer_analysis_source_binding,skill_context}.py` | `tests/` | Tests for retired local inference and selection. |
| `tests/assistance/**` | `tests/assistance/**` | Assistance and LAB-specific test history; LAB tests are candidates for migration to Project-Lab. |
| `tests/as1/test_pc_as1_{central_inference,checkpoint_conversation,deployment,negative_dependencies,response_format_forwarding,runtime_cache_epoch}.py` | `legacy-tests/as1/` | Importer, inference, retired response-detail, deployment and skill-selection tests; these describe removed agentic interfaces. |
| `tests/local_runtime/**` | `legacy-tests/local_runtime/**` | Investigator/runtime lifecycle, backend and local model execution tests. |
| `tests/todo/**` except `test_native_run_retirement.py` | `legacy-tests/todo/**` | Historical in-repository Todo engine tests against an obsolete bundled API; Todo's standalone repository owns current engine tests. The native run retirement contract remains active under `tests/todo/`. |
| `tests/test_{cli_profiles,deployment,device_scheduler_cli,domain_adapters,future_write_seam,maintenance_receipt,plan_apply,supersession_receipt,workflow_lifecycle}.py` | `legacy-tests/top-level/` | Tests for retired CLI, observer inference seam, bundled scheduler commands and old workflow engine imports. The new CLI/profile contract is tested in active `tests/test_cli_profiles.py`; deterministic workflow tests remain active in `tests/test_compact_workflow.py`, `tests/test_workflow_binding.py`, and `tests/as1/test_pc_as1_control.py`. |
| `tests/test_{skill_ingestion,skills}.py` | `legacy-tests/top-level/` | Tests for the archived skill registry and skill-specific archive ingestion; guidance is now ordinary indexed project knowledge. |
| `tests/wf2a_core/test_workflow_core.py` | `legacy-tests/wf2a_core/` | Legacy bundled workflow-core implementation tests; current deterministic workflow tools remain active through the standalone Todo integration. |
| `tests/test_call_audit.py` | active, adapted | Generic bounded audit and caller-attribution coverage retained; inference-specific backend coverage was removed. |
| `README.md` | `README.md` | Original human-facing README preserved here; active root README is being rewritten for the new product. |
| `AGENTS.md` | `AGENTS.md` | Original repository instructions preserved; active instructions describe the deterministic profiles and peer-package boundaries. |
| `docs/development.md` | `docs/development.md` | Original development guide preserved here; active guide is being rewritten. |
| `docs/deployment.md` | `docs/deployment.md` | Original deployment guide preserved here; active guide is being rewritten. |
| Other tracked `docs/**` paths | `docs/**`, enumerated in `DOCUMENT_PATHS.txt` | Historical architecture, inference deployments, profile contracts and qualification artifacts retained byte-for-byte; active documentation has a new current guide index. |
| Retired model/assistance/GPU qualification scripts and inference service templates | original relative paths, enumerated in `RETIRED_OPERATIONS.txt` | Preserve operational history without leaving commands targeting removed model-runtime modules among active tools. |
| `tests/test_pa1_bootstrap_probe.py` | `legacy-tests/top-level/test_pa1_bootstrap_probe.py` | Qualification of the retired PA1/inference public-surface probe; current profile schemas and transport have active replacement acceptance. |
| `uv.lock` | `uv.lock` | Historical dependency lock; active setup resolves local foundations wheels in private scratch rather than committing absolute wheelhouse paths. |

The archive preserves original file contents in this branch; the `c14ad76`
commit remains the canonical source for prior Git history. Existing live
databases, packets, notes, experiment artifacts, and user state are not copied,
moved, initialized, or rewritten by this source archive.

The archived test files are retained as source history, not collected or
treated as tests for the new product. The active suite contains deterministic
coverage for the replacement contracts; archived interfaces require a separate
owner before they are revived.
