# Observer and local assistance: functional activation

The scoped fix is active and the two historical stranded slots have been
recovered through operator-only proof verification. Final checks found capacity
2 total / 0 occupied / 2 available, no active jobs, leases or admissions, no
cleanup degradation, no release veto, and passing health/readiness. Automatic
assistance remains disabled. No public tool surface or service configuration
changed.

## Recovery

The existing source-mode slot reconciler required an installed release pin that
the source launcher removes. The current mixed session state also had a circular
barrier: receiptless sessions blocked release of receipt-bearing sessions;
session reconciliation rejected retained slots; slot reconciliation required
acknowledged session release.

The new internal operator path consumes archived cleanup identities only as
historical support. It independently verifies current PC, Todo and receiver
identity, both units stopped, explicit kernel process absence, empty configured
GPUs, zero host owners/intents/reservations, no active work and exact terminal
failed slots. A HostCoordinator writer fence covers proof verification, audited
session reconciliation, acknowledgement and exact slot cleanup. Four session
records were reconciled before removing the two slots. Existing job records,
close receipts and immutable audits remain preserved.

The first activation attempt rejected the operational helper's missing GPU
binding and rolled back before reconciliation. The helper was retried with the
existing service's exact GPU allowlist and host-runtime path. The successful
stop-to-resume interval was approximately four seconds, at 16:15:25–16:15:29
Europe/Berlin on 2026-10-08. Executable originals, SQLite observation backup and
operational receipts remain under
`.cache/observer-functional-recovery-20261008`; these are not repository data.

## Bounded live acceptance

- Observer investigation completed in about 104 seconds with five findings
  and retained source evidence: the documented PBMC3K legal candidate set,
  regret measurement and explicit benchmark limits. Result packet:
  `pkt_fea289ea33164a038c86fa97a90d6088`.
- Installed `cpp-context-compiler` skill consultation completed in about
  68 seconds with an authoritative verbatim excerpt and synthesis. Connected
  client retrieval also completed. The synthesis's stated line numbers were
  inaccurate; excerpt locators and verbatim source text are the reliable
  evidence. This qualifies functional completion, not general synthesis quality.
- Existing local `assistance ask` completed in about 49 seconds with two
  source-backed findings defining axis identity and typed relations. Result:
  `pkt_a38278a844d24e38b80f3f1073bd05be`.
- Skill and local requests overlapped for about 23 seconds. Afterward both
  broker slots were available and no active native work remained. This is one
  bounded concurrency check, not broad load qualification.
- A fresh configured Codex stdio client initialized with its existing 12-tool
  contract and read a stored packet. The actual connected local client also
  read host memory and returned explicit terminal size limitation for an
  indivisible packet above its standard maximum, without a continuation loop.
- The actual connected observer client retrieved the completed investigation
  and skill result using the identical accepted arguments.

[Functional receipt](2026-10-08-observer-functional-check.json),
[connected-client responses](2026-10-08-observer-functional-connected.json),
[patch](2026-10-08-observer-functional-recovery.patch), and
[file manifest](2026-10-08-observer-functional-recovery-manifest.json) preserve
the reviewable result.

## Validation and limits

Focused CPU recovery, existing slot recovery and owned-release tests passed:
70 tests and 5 subtests. The final scoped review selection passed 13 tests
(9 deselected). The default smoke passed 23 tests earlier in this candidate;
subsequent safety/compatibility edits received focused checks. Counts overlap.
No complete re-review, broad qualification or model comparison was performed.

The original cleanup error is unrecoverable from old boolean-only records.
The new allowlisted diagnostics retain future failure codes. This fix establishes
safe current recovery and successful bounded use, not immunity to every future
close failure. Aggregate recovery proofs older than 300 seconds fail closed;
delayed reattestation after an interrupted operator recovery is deferred.
Local agent synthesis remains advisory and limited in reasoning capability.
