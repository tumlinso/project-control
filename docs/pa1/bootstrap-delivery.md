# PA1 revised bootstrap delivery — 2026-10-07

## Scope and authority

The operator revised `PC-PA1-RUN-1` to deliver the coordination server and
on-demand advisory assistance. Charter version **2**, content hash
`29affd6a0e8bf1c9d3692f17522076e5750ab21fa4a2b12f8870bc4cfb4312ee`, is the
native authority. `PC-PA1-QUALIFY` and `PC-PA1-0000` record delivery evidence;
read their native results and run history for the final completion revision.
The project UUID remains `76dc6bf0-1223-4dda-bf8b-c306fb7721a7`.

LAB is deferred entirely. Its lane entry was skipped using the supported
transaction; its task, implementation, sessions, receipts and historical
acceptance remain. The original PA1/A01–A40 contract has **not** passed.
Autonomous CPU LAB, CUDA lifecycle qualification, broad cancellation/crash
campaigns, performance/model-quality studies and frozen-release qualification
remain follow-up obligations.

## Deployment and normal development

Grounded-assistance serving commit: `e095ee5d0e0ea32bf7dd2013347c1601a77fe1aa`.
A final cold-stop idempotence repair follows that serving check. The final
checkout and process identity are recorded in `delivery-process-identity.json`
in the evidence directory below. Documentation and fixture-only changes do not
change executable inventories. HTTP runs from `/home/tumlinson/project-control` on loopback port
8768 under `project-control.service`, using `scripts/pc-dev run serve observer`.
During grounded assistance verification its restart was 20:19:08 UTC, PID
1553425. Fresh configured stdio uses
`scripts/pc-dev run codex`.

The verified assistance-serving supervisor was PID 1553416, process start 5862955,
daemon epoch `8d0b3cd210d636e74916a99577fe7a40a3c8d7482ac799de6b46b9ac2f5ddc26`.
Assistance-serving PC executable fingerprint:
`55c84f03e058a062b71ea9f82f41017d1629b0bfac0486e378be15196a2f33d9`.
Bundled Todo runtime fingerprint:
`3631fd09d05571107d9c7c26d9597e7d2700959a048abb506a9b5d69f3b5697b`.
The supervisor was then explicitly stopped with verified owner release. HTTP
remains available; the release veto is cleared for the next explicit request.
Automatic preparation remains off. No model is preloaded for delivery.

```sh
scripts/pc-dev setup                  # once, or after dependency changes
scripts/pc-dev run assistance stop    # before editing executable code
# edit source
scripts/pc-dev test                   # small CPU smoke
scripts/pc-dev test tests/test_workflow_binding.py -q
systemctl --user restart project-control-inference.service project-control.service
scripts/pc-dev run assistance resume --release
scripts/pc-dev run assistance ask --project project-control \
  'What are the source setup, test, and restart commands? Cite the current documentation.'
scripts/pc-dev run assistance chat --project project-control
scripts/pc-dev run assistance stop
```

Foreground source entrypoints are `scripts/pc-dev run serve observer --host
127.0.0.1 --port 8768` and `scripts/pc-dev run codex`. Tests do not require a
service, task claim, inference or GPU. Ordinary Python edits need a process
restart, not a wheel build or receiver-manifest refresh. Follow
[development](../development.md), [deployment](../deployment.md), and
[source qualification](source-qualification.md). Existing service/configuration
backups and the prior installed candidate remain available for rollback.

## Actual evidence

Private receipts are under
`/home/tumlinson/project-control/.project-control-test/pa1-bootstrap-20261007/`.
They include source identities and the unmodified returned payloads.

| Required capability | Actual verification |
| --- | --- |
| HTTP and fresh stdio | `deployed-connections.json`: actual initialize/tools/list; health/readiness; registered catalog and project overview through both connections |
| Information tools | `final-live-acceptance.json`, packet-expansion receipts: real overview, search, frontier, evidence, bounded history, task impact and machine observations |
| Workflow and authorization | Temporary-state lifecycle/mutation/rescue/content-independence regressions; production QUALIFY acquired, inspected/coordinated and required smoke run through fresh public stdio |
| Grounded project assistance | `project-answer-dispatch-fixed.json`, `project-public-investigate.json`, `project-chat.txt`: ordinary CLI ask, public investigate and chat ask/exit completed |
| Source attribution | README full-file SHA-256 `c632d2df9d01cf22a0f96064e9c471ff0facd8638de0763d0fc6c69209f5609c`; answer read lines 1–85 and cited the observed packet |
| Skill consultation | `skill-answer.json`: useful actual public cpp-context-compiler answer and observed SKILL.md excerpt; full-file SHA-256 `ad6a88ce98bab68d27372d5858e35ba2eeacfac2ff5a9c579882165bfabc7660`, independently rechecked |
| Inference lifecycle | `status-after-successful-assistance.json`: healthy, one idle resident slot, zero leases/work; `stop-after-successful-assistance.json` and `status-after-final-stop.json`: stopped, zero work/resources, fresh verified owner release |
| Fast source checks | Default smoke 23 passed; changed dispatcher profile selection 6 passed; native required gate `PC-PA1-QUALIFY-SOURCE-SMOKE` passed. Prior focused changed-boundary selections include identity, authorization, content independence, native contract propagation and release accounting; selections overlap and must not be summed |

Optional domain content absence is covered by temporary-state core-read and
rescue regressions. Missing content does not invalidate executable binding.
No full-suite, model calibration, GPU workload or LAB experiment gates this
delivery.

## Repairs, limits and follow-up

The repeated project-answer failure came from workflow stdio frontends
consuming shared inquiry jobs despite having no user systemd bus. Inquiry
profiles now own dispatch; coder/codex and other non-inquiry frontends do not.
The mutator retains its documented investigate capability. Source process
verification also handles the existing protected service namespace without
removing unit hardening or weakening frozen-release checks.

One already-empty stale historical resource row could not retrospectively
obtain a sealed release receipt. The operator explicitly authorized a direct
SQLite rescue. The broker was backed up; exact absent process, empty GPUs,
absent host owner/reservations, zero work and permanent veto were checked. The
single row was administratively marked superseded, preserving its receipt and
NULL release proof, with a separate audit:
`399956002fe4281606dddf2b8b1b28dceb1689ed246eb7305cf9e2d10c7efe19`.
This is **not** a verified historical release. The later fresh assistance run
produced a real owner release under request
`55311957d316152f92b5ee44216043f6`. A durable supported administrative retirement
operation and retention of cleanup receipts after slot removal are follow-up
recovery improvements.

Information receipts retain bounded coverage warnings: optional authored
orientation is absent; Git history is unavailable in the tested environment;
task impact reports gaps rather than asserting a complete source-call or
cross-project graph. Overview's singular active-run choice can select another
valid active run; use native inspect/frontier for PA1 ownership. These limitations
do not prevent the tested core operations. Old private inquiry failure fields
may remain in a successfully retried job's history; inspect its final status and
answer instead of treating a historical error as a current failure.

The isolated scripted public-inquiry test stalled despite a persisted completed
answer; bounded attempts were stopped, and the original test assertions were
preserved without a skip or timeout-based pass. This is an unresolved fixture
lifecycle issue, not evidence that all collected source tests pass. The actual
CLI, public investigate and chat requests above completed successfully.
Repeated stop's inactive/no-owned case has separate strict-zero-census
regressions (42 demand-runtime tests passed); it does not mint a new owner proof.
