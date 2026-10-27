# Lifecycle evidence and read-only reproduction

This is a diagnostic recipe for the existing active observer broker only. It opens SQLite in URI `mode=ro`, does not issue MCP calls, and does not modify the database. Active DB: `/home/tumlinson/.cache/project-control/as1-observer-analysis/as1/jobs-v2/jobs.sqlite3` (observed mtime 2026-10-08T12:22:50Z). Current source identity was `d8c45ebf7991e9338da5e6971ac0be7ccfa455ae`; running observer PID 34150 had cwd `/home/tumlinson/project-control`. Current supervisor status named the same `.cache/.../as1-observer-analysis` state root and reported `runtime_mode=source`.

## Minimal read-only SQL

Connect as:

```python
con = sqlite3.connect(
    "file:/home/tumlinson/.cache/project-control/as1-observer-analysis/as1/jobs-v2/jobs.sqlite3?mode=ro",
    uri=True,
)
con.execute("PRAGMA query_only=ON")
con.row_factory = sqlite3.Row
```

Inquiry identity index and request/job mapping:

```sql
SELECT i.identity, j.id, j.updated,
       json_extract(j.record,'$.status') AS status,
       json_extract(j.record,'$.mode') AS mode,
       json_extract(j.record,'$.question') AS question,
       json_extract(j.record,'$.attempt') AS attempt,
       json_extract(j.record,'$.terminal_reason') AS terminal_reason,
       json_extract(j.record,'$.answer') AS answer,
       j.observations, j.request_id, j.request_hash, j.inquiry
FROM inquiry_index AS i JOIN jobs AS j ON j.id=i.job
WHERE j.id IN ('job_c2cc682affe648b0bad891f1936a95f5',
               'job_311af2e241064cb796ffaefb3d12cf5b');
```

Execution slots and owner/cleanup protection:

```sql
SELECT job, attempt, lease, owner_pid, owner_start, cleanup_failed
FROM execution_slots ORDER BY job;
```

Frame state and turn/attempt counters:

```sql
SELECT job_id, generation, state, deadline, turns_used, failed_attempts,
       turns_reserved, terminal_status, terminal_result, updated
FROM pa1_frames
WHERE job_id IN ('job_c2cc682affe648b0bad891f1936a95f5',
                 'job_311af2e241064cb796ffaefb3d12cf5b',
                 'job_ccb645b85cac4e7ab4118a2488ab8b49',
                 'job_cbdec88c0ea240a9a37a13bb99c13c25');

SELECT job, attempt, generation, policy_id, requested, telemetry
FROM pa1_frame_attempts
WHERE job IN ('job_c2cc682affe648b0bad891f1936a95f5',
              'job_311af2e241064cb796ffaefb3d12cf5b',
              'job_ccb645b85cac4e7ab4118a2488ab8b49',
              'job_cbdec88c0ea240a9a37a13bb99c13c25')
ORDER BY job, attempt;
```

Call-audit filter used the owner-only existing JSONL `/home/tumlinson/.cache/project-control/call-audit/calls.jsonl`. Parse each line as JSON; keep only `event=tool_call`, `tool=investigate`, and `arguments.question_excerpt` containing the distinctive PBMC3K or “same cell-by-gene” phrase. For `model_request` events, keep only `operation=investigate_turn` and matching `messages.question_excerpt`. Emit timestamps, call ID, phase, outcome and duration only; audit stores no tool response body. Do not dump whole lines or messages.

## Recovered results

The two incident inquiries are in the SQL above. At terminal time both had no answer, findings, result packet or observations. PBMC3K (`job_c2...`) ended `failed/deadline_exhausted` at 12:22:14.258Z with DurableJob attempt=1, frame expired and turns_used=0. Same-cell/different-relations (`job_311...`) ended `failed/attempt_or_deadline_exhausted` at 12:22:50.065Z with attempt=0, frame still queued and turns_used=0. Neither has frame-attempt rows. The attempt=1 versus zero frame counters is unexplained.

Two old terminal jobs held the global execution slots: `job_ccb645...` attempt 2 and `job_cbdec88...` attempt 3. Both became `partial/deadline_exhausted` at 11:16:59.619Z; slots stayed with `cleanup_failed=1`, owner PID 34150/start 144964. `claim()` protects those slots while owner identity remains alive. The cleanup failure’s exact error code is not persisted.

A recovered simpler success in this same active DB is `job_9c95f5e934c441ca92e3014ec64ab8ba`, question “Briefly identify the purpose of Cellerator.” It was created 11:04:27.771Z and completed at 11:05:04.442Z on attempt 2 with an answer, three findings, result packet `pkt_658f9fe5cd184bc1bbb1c42f8f35ac45`, evidence packet `pkt_b588053d49cc42198efa72df188a1b49`. Call audit shows that inquiry completed in the tool surface (do not treat this separate simple success as evidence the later substantive jobs should also finish).

Earlier failures in the same retained DB include `job_6d6fb31c7fb94cc6ae98ae27b9dacd9c`, terminal `failed` on 2026-10-05 10:52:47Z with raw reason `Extra data: line 2 column 1 (char 131)`; `job_d6d9e2fc8c634b23802d8008db2ef0a7`, `partial`, no answer, `Extra data: line 1 column 155 (char 154)`; and empty partial jobs `job_1b073443353448a8bb0a0c29ccafc579`/`job_f81b6fa5f4fe4aff8342db62ba6dfb07` with `finding_requires_observed_packet`, plus `job_09323aa9adee482aa3cee7df0087d522` with `context_budget`. Source maps terminal unusable/negative-cache retries to `analysis_unavailable`; these historical parse/citation/context causes differ from the current Oct 8 slot starvation. These records let us substantiate earlier analysis_unavailable semantics, but do not recover the external client’s exact response transcript.

## Interpretation boundary

The live rows establish server-side queued/expired work, exact negative cache state and retained slots. The audit confirms that repeated individual observer calls returned promptly and no model requests occurred for the two Oct 8 inquiries. It does not establish the external client’s response bodies, larger-batch timeout stack, or why cleanup failed. The simpler completed inquiry is real, separate evidence, not proof of general concurrency health.
