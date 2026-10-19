"""SQLite schema and forward-only migrations."""

from __future__ import annotations

PROJECT_SCHEMA_VERSION = 2
SCHEMA_VERSION = PROJECT_SCHEMA_VERSION  # Preserved public/on-disk compatibility alias.

MIGRATION_1 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, token_hash TEXT NOT NULL UNIQUE,
  external_id TEXT, hostname TEXT NOT NULL, pid INTEGER, process_start TEXT,
  repo_root TEXT NOT NULL, worktree_root TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, parent_id TEXT REFERENCES tasks(id), kind TEXT NOT NULL,
  title TEXT NOT NULL, objective TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned',
  priority INTEGER NOT NULL DEFAULT 0, tags_json TEXT NOT NULL DEFAULT '[]',
  parallel_policy TEXT NOT NULL DEFAULT 'serial', result TEXT, next_action TEXT NOT NULL DEFAULT '',
  result_policy_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT '', legacy_owner TEXT, legacy_payload_json TEXT NOT NULL DEFAULT '{}',
  attention_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks(status, priority DESC, id);
CREATE TABLE IF NOT EXISTS decisions(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, value_json TEXT, allowed_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_dependencies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  type TEXT NOT NULL, prerequisite_task_id TEXT REFERENCES tasks(id), checkpoint_id TEXT,
  interface_id TEXT, barrier_id TEXT, decision_id TEXT, condition_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(task_id,type,prerequisite_task_id,checkpoint_id,interface_id,barrier_id,decision_id)
);
CREATE INDEX IF NOT EXISTS idx_dependencies_task ON task_dependencies(task_id);
CREATE TABLE IF NOT EXISTS checkpoints(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), title TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', reached_at TEXT, revoked_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS barriers(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'all', quorum INTEGER,
  state TEXT NOT NULL DEFAULT 'closed', explanation TEXT NOT NULL DEFAULT '', opened_at TEXT,
  revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS barrier_requirements(
  id INTEGER PRIMARY KEY AUTOINCREMENT, barrier_id TEXT NOT NULL REFERENCES barriers(id) ON DELETE CASCADE,
  type TEXT NOT NULL, entity_id TEXT NOT NULL, required_state TEXT NOT NULL,
  dispositions_json TEXT NOT NULL DEFAULT '[]', UNIQUE(barrier_id,type,entity_id,required_state)
);
CREATE TABLE IF NOT EXISTS interfaces(
  id TEXT PRIMARY KEY, owner_task_id TEXT NOT NULL REFERENCES tasks(id), state TEXT NOT NULL DEFAULT 'draft',
  version TEXT NOT NULL DEFAULT '0', contract_paths_json TEXT NOT NULL DEFAULT '[]', content_hash TEXT,
  frozen_at TEXT, revised_at TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS interface_consumers(
  interface_id TEXT NOT NULL REFERENCES interfaces(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  required_state TEXT NOT NULL DEFAULT 'frozen', required_version TEXT,
  PRIMARY KEY(interface_id,task_id)
);
CREATE TABLE IF NOT EXISTS checkpoint_interfaces(
  checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id) ON DELETE CASCADE,
  interface_id TEXT NOT NULL REFERENCES interfaces(id) ON DELETE CASCADE,
  version TEXT, PRIMARY KEY(checkpoint_id,interface_id)
);
CREATE TABLE IF NOT EXISTS invariants(
  id TEXT PRIMARY KEY, rule TEXT NOT NULL, scope_json TEXT NOT NULL DEFAULT '{}',
  severity TEXT NOT NULL DEFAULT 'error', enforcement TEXT
);
CREATE TABLE IF NOT EXISTS task_invariants(
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  invariant_id TEXT NOT NULL REFERENCES invariants(id) ON DELETE CASCADE,
  PRIMARY KEY(task_id,invariant_id)
);
CREATE TABLE IF NOT EXISTS ownership_scopes(
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  mode TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(task_id,mode,path)
);
CREATE INDEX IF NOT EXISTS idx_scope_path ON ownership_scopes(path,mode);
CREATE TABLE IF NOT EXISTS task_artifacts(
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, path TEXT NOT NULL, content_hash TEXT,
  PRIMARY KEY(task_id,kind,path)
);
CREATE TABLE IF NOT EXISTS named_locks(name TEXT PRIMARY KEY, capacity INTEGER NOT NULL DEFAULT 1, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS task_locks(
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, lock_name TEXT NOT NULL REFERENCES named_locks(name),
  phase TEXT NOT NULL DEFAULT 'claim', PRIMARY KEY(task_id,lock_name,phase)
);
CREATE TABLE IF NOT EXISTS claims(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), session_id TEXT NOT NULL REFERENCES sessions(id),
  token_hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL, created_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, baseline_head TEXT, baseline_manifest_json TEXT NOT NULL DEFAULT '{}',
  baseline_revision INTEGER NOT NULL, orphan_reason TEXT, released_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_claim_per_task ON claims(task_id) WHERE state='active';
CREATE INDEX IF NOT EXISTS idx_claims_state_expiry ON claims(state,expires_at);
CREATE TABLE IF NOT EXISTS lock_leases(
  id TEXT PRIMARY KEY, lock_name TEXT NOT NULL REFERENCES named_locks(name), claim_id TEXT REFERENCES claims(id),
  session_id TEXT NOT NULL REFERENCES sessions(id), token_hash TEXT NOT NULL, state TEXT NOT NULL,
  acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_exclusive_lock ON lock_leases(lock_name) WHERE state='active';
CREATE TABLE IF NOT EXISTS resource_classes(
  id TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'exclusive', metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS resource_instances(
  id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES resource_classes(id), capacity INTEGER NOT NULL DEFAULT 1,
  hostname TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_resources_class ON resource_instances(class_id,enabled,id);
CREATE TABLE IF NOT EXISTS resource_requests(
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, gate_id TEXT,
  phase TEXT NOT NULL, selector TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 1,
  mode TEXT NOT NULL DEFAULT 'exclusive', required INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS resource_leases(
  id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES resource_instances(id), claim_id TEXT REFERENCES claims(id),
  session_id TEXT NOT NULL REFERENCES sessions(id), request_id TEXT, token_hash TEXT NOT NULL,
  state TEXT NOT NULL, hostname TEXT NOT NULL, pid INTEGER, process_start TEXT, command_json TEXT,
  acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL, released_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_resource_leases_active ON resource_leases(instance_id,state,expires_at);
CREATE TABLE IF NOT EXISTS gates(
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
  checkpoint_id TEXT REFERENCES checkpoints(id) ON DELETE CASCADE, type TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}', required INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending', valid INTEGER NOT NULL DEFAULT 0,
  input_fingerprint TEXT, last_run_at TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checkpoint_gates(
  checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id) ON DELETE CASCADE,
  gate_id TEXT NOT NULL REFERENCES gates(id) ON DELETE CASCADE,
  PRIMARY KEY(checkpoint_id,gate_id)
);
CREATE TABLE IF NOT EXISTS evidence(
  id TEXT PRIMARY KEY, gate_id TEXT REFERENCES gates(id), checkpoint_id TEXT REFERENCES checkpoints(id),
  claim_id TEXT REFERENCES claims(id), kind TEXT NOT NULL, status TEXT NOT NULL,
  path TEXT, content_hash TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS handoffs(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), claim_id TEXT REFERENCES claims(id),
  kind TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS migration_warnings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, code TEXT NOT NULL, message TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS projection_status(
  name TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0, generated_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL UNIQUE, timestamp TEXT NOT NULL,
  actor_session_id TEXT REFERENCES sessions(id), entity_type TEXT NOT NULL, entity_id TEXT,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL
);
"""

MIGRATION_2 = r"""
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type,entity_id,revision);
CREATE INDEX IF NOT EXISTS idx_evidence_gate ON evidence(gate_id,created_at);
"""

MIGRATION_3 = r"""
ALTER TABLE lock_leases ADD COLUMN hostname TEXT;
ALTER TABLE lock_leases ADD COLUMN pid INTEGER;
ALTER TABLE lock_leases ADD COLUMN process_start TEXT;
ALTER TABLE lock_leases ADD COLUMN command_json TEXT;
CREATE INDEX IF NOT EXISTS idx_lock_leases_expiry ON lock_leases(state,expires_at);
"""

MIGRATION_4 = r"""
CREATE TABLE IF NOT EXISTS child_executions(
  id TEXT PRIMARY KEY,
  parent_claim_id TEXT NOT NULL REFERENCES claims(id),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  objective TEXT NOT NULL,
  gates_json TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  heartbeat_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_child_executions_parent
  ON child_executions(parent_claim_id,state,created_at);
CREATE TABLE IF NOT EXISTS child_scope_leases(
  child_execution_id TEXT NOT NULL REFERENCES child_executions(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',
  acquired_at TEXT NOT NULL,
  released_at TEXT,
  PRIMARY KEY(child_execution_id,path)
);
CREATE INDEX IF NOT EXISTS idx_child_scope_leases_active
  ON child_scope_leases(path,state);
CREATE TABLE IF NOT EXISTS child_attempts(
  id TEXT PRIMARY KEY,
  child_execution_id TEXT NOT NULL REFERENCES child_executions(id) ON DELETE CASCADE,
  attempt_number INTEGER NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  completed_at TEXT,
  result_json TEXT,
  UNIQUE(child_execution_id,attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_child_attempts_expiry
  ON child_attempts(state,expires_at);
"""

MIGRATION_5 = r"""
ALTER TABLE child_executions ADD COLUMN candidate_gates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE child_executions ADD COLUMN acceptance_gates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE child_executions ADD COLUMN result_refs_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE child_attempts ADD COLUMN result_refs_json TEXT NOT NULL DEFAULT '{}';
"""

MIGRATION_6 = r"""
ALTER TABLE child_executions ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'write';
ALTER TABLE child_executions ADD COLUMN authorized_scopes_json TEXT NOT NULL DEFAULT '[]';
"""

MIGRATION_7 = r"""
ALTER TABLE claims ADD COLUMN owner_system TEXT;
ALTER TABLE claims ADD COLUMN owner_instance_id TEXT;
CREATE TABLE IF NOT EXISTS live_recovery_approvals(
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
  repo_root TEXT NOT NULL, project_uuid TEXT NOT NULL, task_id TEXT NOT NULL,
  claim_fingerprint TEXT NOT NULL, project_revision INTEGER NOT NULL,
  requester_uid INTEGER NOT NULL, approver_identity TEXT NOT NULL, reason TEXT NOT NULL,
  prior_instance_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_recovery_approval_state
  ON live_recovery_approvals(state,expires_at);
CREATE TABLE IF NOT EXISTS live_recovery_audit(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
  prior_claim_fingerprint TEXT NOT NULL, new_claim_fingerprint TEXT NOT NULL,
  approver_identity TEXT NOT NULL, requester_uid INTEGER NOT NULL, reason TEXT NOT NULL,
  approved_at TEXT NOT NULL, consumed_at TEXT NOT NULL,
  prior_instance_id TEXT NOT NULL, new_instance_id TEXT NOT NULL,
  disposition TEXT NOT NULL, project_revision INTEGER NOT NULL
);
"""

MIGRATION_8 = r"""
ALTER TABLE live_recovery_approvals ADD COLUMN approval_kind TEXT NOT NULL DEFAULT 'live_claim_override';
ALTER TABLE live_recovery_approvals ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE live_recovery_audit ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';
"""

MIGRATION_9 = r"""
ALTER TABLE tasks ADD COLUMN completion_revision INTEGER;
ALTER TABLE tasks ADD COLUMN completion_git_head TEXT;
ALTER TABLE tasks ADD COLUMN completion_commit TEXT;
CREATE TABLE IF NOT EXISTS task_completion_gates(
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  gate_id TEXT NOT NULL REFERENCES gates(id) ON DELETE CASCADE,
  status TEXT NOT NULL, valid INTEGER NOT NULL,
  input_fingerprint TEXT, evidence_id TEXT REFERENCES evidence(id),
  evidence_revision INTEGER, validation_git_head TEXT,
  completion_revision INTEGER NOT NULL, completion_git_head TEXT,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(task_id,gate_id)
);
CREATE INDEX IF NOT EXISTS idx_task_completion_gates_revision
  ON task_completion_gates(task_id,completion_revision);
"""

MIGRATION_10 = r"""
CREATE TABLE IF NOT EXISTS workflow_runs(
  id TEXT PRIMARY KEY,
  root_task_id TEXT REFERENCES tasks(id),
  status TEXT NOT NULL DEFAULT 'active',
  active_charter_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
  ON workflow_runs(status,updated_at,id);
CREATE TABLE IF NOT EXISTS workflow_run_charters(
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  content_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  creation_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  superseded_at TEXT,
  superseded_revision INTEGER,
  PRIMARY KEY(run_id,version),
  UNIQUE(run_id,content_hash)
);
CREATE TABLE IF NOT EXISTS workflow_lanes(
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  parent_lane_id TEXT REFERENCES workflow_lanes(id),
  role TEXT NOT NULL CHECK(role IN ('coordinator','implementer','validator','integrator','specialist')),
  state TEXT NOT NULL DEFAULT 'ready',
  context_cursor INTEGER NOT NULL DEFAULT 0,
  workspace_mode TEXT NOT NULL DEFAULT 'exclusive'
    CHECK(workspace_mode IN ('exclusive','read_shared','isolated_merge','contract_split')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  UNIQUE(run_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_root_lane_per_run
  ON workflow_lanes(run_id) WHERE parent_lane_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_lanes_parent
  ON workflow_lanes(run_id,parent_lane_id,id);
CREATE TABLE IF NOT EXISTS workflow_lane_tasks(
  lane_id TEXT NOT NULL REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  position INTEGER NOT NULL CHECK(position >= 0),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  state TEXT NOT NULL DEFAULT 'queued',
  enqueued_at TEXT NOT NULL,
  activated_at TEXT,
  completed_at TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(lane_id,position),
  UNIQUE(lane_id,task_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_task_per_lane
  ON workflow_lane_tasks(lane_id) WHERE state='active';
CREATE TABLE IF NOT EXISTS workflow_workspaces(
  id TEXT PRIMARY KEY,
  repository_identity TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  lane_id TEXT NOT NULL REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK(mode IN ('exclusive','read_shared','isolated_merge','contract_split')),
  base_commit TEXT NOT NULL,
  worktree_path TEXT,
  branch TEXT,
  state TEXT NOT NULL,
  integration_task_id TEXT REFERENCES tasks(id),
  artifact_kind TEXT,
  artifact_ref TEXT,
  diff_hash TEXT,
  merge_result_json TEXT NOT NULL DEFAULT '{}',
  cleanup_eligible INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(run_id,lane_id)
);
CREATE TABLE IF NOT EXISTS workflow_dispatches(
  id TEXT PRIMARY KEY,
  lane_id TEXT NOT NULL REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  claim_id TEXT NOT NULL REFERENCES claims(id),
  workspace_id TEXT REFERENCES workflow_workspaces(id),
  state TEXT NOT NULL DEFAULT 'active',
  context_version INTEGER NOT NULL,
  heartbeat_at TEXT NOT NULL,
  hostname TEXT,
  pid INTEGER,
  process_start TEXT,
  created_at TEXT NOT NULL,
  released_at TEXT,
  revision INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_dispatch_per_lane
  ON workflow_dispatches(lane_id) WHERE state='active';
CREATE UNIQUE INDEX IF NOT EXISTS one_active_dispatch_per_session
  ON workflow_dispatches(session_id) WHERE state='active';
CREATE TABLE IF NOT EXISTS workflow_capabilities(
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  capability_class TEXT NOT NULL CHECK(capability_class IN ('first_class','child')),
  project_uuid TEXT NOT NULL,
  repository_identity TEXT NOT NULL,
  session_id TEXT REFERENCES sessions(id),
  claim_id TEXT REFERENCES claims(id),
  run_id TEXT REFERENCES workflow_runs(id),
  lane_id TEXT REFERENCES workflow_lanes(id),
  role TEXT,
  task_id TEXT REFERENCES tasks(id),
  parent_capability_id TEXT REFERENCES workflow_capabilities(id),
  child_execution_id TEXT REFERENCES child_executions(id),
  allowed_operations_json TEXT NOT NULL DEFAULT '[]',
  incarnation INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  CHECK(
    (capability_class='first_class' AND session_id IS NOT NULL AND claim_id IS NOT NULL
      AND run_id IS NOT NULL AND lane_id IS NOT NULL AND role IS NOT NULL
      AND task_id IS NOT NULL AND parent_capability_id IS NULL AND child_execution_id IS NULL)
    OR
    (capability_class='child' AND parent_capability_id IS NOT NULL
      AND child_execution_id IS NOT NULL AND run_id IS NULL AND lane_id IS NULL AND role IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_workflow_capability_lineage
  ON workflow_capabilities(project_uuid,repository_identity,session_id,claim_id,run_id,lane_id,task_id,state);
CREATE TABLE IF NOT EXISTS workflow_messages(
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  author_lane_id TEXT NOT NULL REFERENCES workflow_lanes(id),
  task_id TEXT REFERENCES tasks(id),
  kind TEXT NOT NULL CHECK(kind IN ('status','question','answer','decision','interface_change','conflict','artifact','handoff','rendezvous_arrival')),
  payload_json TEXT NOT NULL,
  references_json TEXT NOT NULL DEFAULT '[]',
  blocking INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'open',
  linked_message_id TEXT REFERENCES workflow_messages(id),
  revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_messages_run_revision
  ON workflow_messages(run_id,revision,id);
CREATE TABLE IF NOT EXISTS workflow_message_recipients(
  message_id TEXT NOT NULL REFERENCES workflow_messages(id) ON DELETE CASCADE,
  recipient_type TEXT NOT NULL CHECK(recipient_type IN ('lane','role','task','run')),
  recipient_id TEXT NOT NULL,
  PRIMARY KEY(message_id,recipient_type,recipient_id)
);
CREATE TABLE IF NOT EXISTS workflow_message_receipts(
  message_id TEXT NOT NULL REFERENCES workflow_messages(id) ON DELETE CASCADE,
  lane_id TEXT NOT NULL REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  received_revision INTEGER NOT NULL,
  received_at TEXT NOT NULL,
  PRIMARY KEY(message_id,lane_id)
);
CREATE TABLE IF NOT EXISTS workflow_rendezvous(
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  barrier_id TEXT REFERENCES barriers(id),
  mode TEXT NOT NULL CHECK(mode IN ('all','quorum','producers')),
  quorum INTEGER,
  join_task_id TEXT NOT NULL REFERENCES tasks(id),
  state TEXT NOT NULL DEFAULT 'closed',
  required_roles_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  opened_at TEXT,
  revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workflow_rendezvous_participants(
  rendezvous_id TEXT NOT NULL REFERENCES workflow_rendezvous(id) ON DELETE CASCADE,
  lane_id TEXT NOT NULL REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  producer INTEGER NOT NULL DEFAULT 0,
  required INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(rendezvous_id,lane_id)
);
CREATE TABLE IF NOT EXISTS workflow_rendezvous_arrivals(
  rendezvous_id TEXT NOT NULL REFERENCES workflow_rendezvous(id) ON DELETE CASCADE,
  lane_id TEXT NOT NULL REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  summary TEXT NOT NULL,
  base_source_identity TEXT,
  final_source_identity TEXT,
  artifact_json TEXT NOT NULL DEFAULT '{}',
  interfaces_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  context_version INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'valid',
  arrived_at TEXT NOT NULL,
  revision INTEGER NOT NULL,
  PRIMARY KEY(rendezvous_id,lane_id)
);
CREATE TABLE IF NOT EXISTS workflow_context_fragments(
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  lane_id TEXT REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('run_charter','lane_brief','task_brief','decision_ledger','delta_inbox','source_packet_ref')),
  owner_scope_json TEXT NOT NULL,
  version INTEGER NOT NULL,
  content_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  creation_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  invalidated_at TEXT,
  invalidation_revision INTEGER,
  superseded_by TEXT REFERENCES workflow_context_fragments(id),
  UNIQUE(run_id,lane_id,task_id,kind,version),
  UNIQUE(run_id,lane_id,task_id,kind,content_hash)
);
CREATE INDEX IF NOT EXISTS idx_workflow_fragments_owner
  ON workflow_context_fragments(run_id,lane_id,task_id,kind,version);
CREATE TABLE IF NOT EXISTS workflow_patch_artifacts(
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workflow_workspaces(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL CHECK(kind IN ('commit','patch')),
  artifact_ref TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  base_commit TEXT NOT NULL,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS workflow_integration_queue(
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  integration_task_id TEXT NOT NULL REFERENCES tasks(id),
  integrator_lane_id TEXT NOT NULL REFERENCES workflow_lanes(id),
  patch_artifact_id TEXT NOT NULL REFERENCES workflow_patch_artifacts(id),
  position INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued',
  conflict_json TEXT NOT NULL DEFAULT '{}',
  merge_result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(run_id,integration_task_id,position),
  UNIQUE(run_id,patch_artifact_id)
);
CREATE TABLE IF NOT EXISTS workflow_child_result_candidates(
  id TEXT PRIMARY KEY,
  child_execution_id TEXT NOT NULL REFERENCES child_executions(id) ON DELETE CASCADE,
  parent_claim_id TEXT NOT NULL REFERENCES claims(id),
  kind TEXT NOT NULL CHECK(kind IN ('candidate_patch','test_result','performance_measurement','source_finding','review_finding','diagnostic_finding')),
  payload_json TEXT NOT NULL,
  artifact_refs_json TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'collected',
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decision_revision INTEGER
);
CREATE INDEX IF NOT EXISTS idx_workflow_child_candidates_parent
  ON workflow_child_result_candidates(parent_claim_id,state,created_at);
CREATE TABLE IF NOT EXISTS workflow_recovery_audit(
  id TEXT PRIMARY KEY,
  project_uuid TEXT NOT NULL,
  run_id TEXT REFERENCES workflow_runs(id),
  lane_id TEXT REFERENCES workflow_lanes(id),
  task_id TEXT REFERENCES tasks(id),
  reason TEXT NOT NULL,
  proposed_plan_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  actor_identity TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  revision INTEGER NOT NULL
);
"""

# Context notes share the existing revisioned fragment ledger rather than
# introducing a second persistent-notes authority.  SQLite cannot add the
# required check/unique constraints in place, so rebuild this one table while
# retaining every legacy row and its identifiers/version history.
MIGRATION_11 = r"""
DROP INDEX IF EXISTS idx_workflow_fragments_owner;
ALTER TABLE workflow_context_fragments RENAME TO workflow_context_fragments_legacy;
CREATE TABLE workflow_context_fragments(
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  lane_id TEXT REFERENCES workflow_lanes(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('run_charter','lane_brief','task_brief','decision_ledger','delta_inbox','source_packet_ref','context_note')),
  owner_scope_json TEXT NOT NULL,
  series_key TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL,
  content_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  creation_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  invalidated_at TEXT,
  invalidation_revision INTEGER,
  superseded_by TEXT REFERENCES workflow_context_fragments(id),
  UNIQUE(run_id,lane_id,task_id,kind,series_key,version),
  UNIQUE(run_id,lane_id,task_id,kind,series_key,content_hash)
);
INSERT INTO workflow_context_fragments(id,run_id,lane_id,task_id,kind,owner_scope_json,series_key,version,content_json,content_hash,creation_revision,created_at,invalidated_at,invalidation_revision,superseded_by)
  SELECT id,run_id,lane_id,task_id,kind,owner_scope_json,'',version,content_json,content_hash,creation_revision,created_at,invalidated_at,invalidation_revision,superseded_by
  FROM workflow_context_fragments_legacy;
DROP TABLE workflow_context_fragments_legacy;
CREATE INDEX IF NOT EXISTS idx_workflow_fragments_owner
  ON workflow_context_fragments(run_id,lane_id,task_id,kind,series_key,version);
"""

# Project declarations share the existing Todo authority and revision/event stream.
MIGRATION_12 = r"""
CREATE TABLE IF NOT EXISTS project_declarations(
  kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
  payload_json TEXT NOT NULL, origin TEXT NOT NULL,
  task_id TEXT REFERENCES tasks(id), retired INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, revision INTEGER NOT NULL,
  PRIMARY KEY(kind,id,version)
);
CREATE TABLE IF NOT EXISTS project_amendments(
  operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
  request_json TEXT NOT NULL, review_json TEXT NOT NULL,
  receipt_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_fragment_invalidations(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
  revision INTEGER NOT NULL, reason TEXT NOT NULL
);
"""

MIGRATIONS = {
  1: MIGRATION_1, 2: MIGRATION_2, 3: MIGRATION_3, 4: MIGRATION_4,
  5: MIGRATION_5, 6: MIGRATION_6, 7: MIGRATION_7, 8: MIGRATION_8,
  9: MIGRATION_9, 10: MIGRATION_10, 11: MIGRATION_11, 12: MIGRATION_12,
}

DATABASE_MIGRATION_VERSION = max(MIGRATIONS)

# Existing core readers need migration 11; semantic declarations need 12.
READ_COMPATIBLE_MIGRATION_VERSION = 11
