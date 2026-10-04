# Skills adapter and GPU resource cooperation

## One observer adapter, native use elsewhere

The final public skill interface is `skill(query?, skill?, hints?, request_id?, job_id?, detail?)`. No parallel `skill_list`, `skill_read`, or `skill_context` vocabulary on the new model surface. A catalog request returns all accessible installed skill names/descriptions with completeness/continuation; it does not silently omit rejected/unsupported registrations. Core catalog lookup can be deterministic without loading a GPU.

Coder, mutator and local scout read installed skills natively with their command/filesystem capabilities. Remove skill text that sends those roles back through observer-only `skill_context/skill_read`. Change routing/front-door instructions, not technical guidance content unrelated to this redesign. Relevant native skill usage must be publishable through the existing context/coordination path without a separate tracking ritual.

The local adapter is a special mode of the same read-only worker, **homed in the registered canonical skills directory**. It reads SKILL.md and native routing maps, follows documented branches/prerequisites, and proposes exact source selections. An omitted skill name allows catalog discovery across accessible skills. A supplied name scopes normal navigation to that skill, with explicit cross-skill dependencies identified rather than silently dropped.

Do not create a competing generic router that overrides a skill's native routing. Project Control owns dispatch, access, identity, budgets and packing; **skill-authored routing knowledge drives resource selection**. The model can reason about which route fits the question. It need not treat a flat lexical hit list as the answer.

## Source-selection manifest, then direct read

A completed selection proposes entries with canonical skill ID/name, resource-relative path, full source content hash, exact range or stable section anchor, selection reason, and prerequisite/cross-reference relationships. The broker validates the root/path and re-reads the source directly. Ranges must be current against the selected hash; if the resource changed, reselect/refresh or return explicit partial authority, not a silently shifted quote.

The returned packet contains:

* a small labeled local-agent synthesis explaining relevance and relationships;
* exact directly read excerpts with skill/resource/range/content identity;
* mapping from synthesis assertions to their excerpts;
* relevant prerequisites, omissions, uncertainty and continuation;
* a stable packet alias reusable as a hint.

The local model's output is never the authoritative copy of the skill text. Preserve exact source text, including meaningful whitespace/code. Do not glue noncontiguous fragments into a fabricated paragraph. If security rules require redacting a resource, return a clearly identified omission/redacted preview, not a falsely verbatim authority block. Direct-read mismatch, out-of-range or symlink escape fails that selection while retaining other valid ones.

A tiny synthesis is useful; unlimited model paraphrase is not. Set a small separate synthesis budget (initially roughly 10–15% of the response, tuned with tests); the exact excerpt budget carries authority. This is a design default, not a reason to drop necessary context. Prerequisites may be supplied by explicit retained hint excerpts instead of repeated copies; record that dependency.

## CUDA and the existing atlas

Reuse `skills.py`, `skill_context.py`, registry/index/ingestion components and the existing skill corpus manifests and graph. Do not flatten or rewrite the CUDA hierarchy or V100 atlas to make the router easier. Traverse architecture/workload/topic/mechanism routes and nested atlas links. Existing resource IDs, graph edges, card bodies, evidence/uncertainty/prerequisite relationships, compendium and source ZIP must be preserved unless a specifically identified path/reference defect needs a minimal repair.

The live baseline has `cuda/SKILL.md` routing native agents through the existing observer `skill_context/skill_read`. This needs profile-aware correction. Changes to front-door guidance and metadata are in scope. Auditing or “deduplicating” the scientific/technical corpus is not. Capture actual baseline resource counts/hashes before editing; do not assert old historical counts as current without recomputing.

Skill adapter can search/index to accelerate navigation, but follows native skill instruction semantics. Test architecture selection, nested routes, prerequisite inclusion, cross-skill references, absent map fallback, stale-resource changes during assembly and exact text equality. Use the real CUDA corpus for read-only qualification and fixtures for forced mutations.

## Applied skill relevance

Native agent publishes a lightweight semantic context record only when a skill materially informed work: skill identity/hash, task/run, relevant project paths/subsystem, topic/route, why applied, and supporting result/decision refs if any. Automatic read telemetry is merely “consulted,” not durable relevance. Prefer recording alongside an existing context publication, handoff or finish operation.

`overview(extended)` reports relevant skills/routes and why. Standard overview can show a small useful set. `search` includes compacted, attributed guidance from established relevant skill use. Search summaries are not substitutes for exact authority; their packet aliases can seed skill() hints. Skill updates invalidate or mark the corresponding guidance for review without erasing what was used historically.

## GPU interlock: extend the existing path

The observed local-worker resource policy already defines idle model residency, cooperative preemption, draining, process-group termination, quiescence and physical lease release. The CUDA controller already distinguishes foreground work from background activity and uses host-global interlocks. Reuse these paths; the required change is making the observer/skill-job services participate reliably in them.

Physical GPU/profiler/host-pressure authority remains host-global. No separate observer reservation table should claim ownership independent of it. Startup does not reserve GPUs/load models/scan corpora merely because tools are listed. Reuse compatible idle residency on demand. Queue admission is independent of GPU admission.

Foreground coder correctness/testing/profiling work can reclaim idle warm observer residency on overlapping devices. Eviction must transition residency to draining, refuse new work in that slot, terminate/release only the owned model process and service lease, observe quiescence/VRAM release, then let the controller acquire the GPU. Do not release a lease merely because a timer expired while a live model still owns memory. Non-overlapping islands need not be evicted.

Active jobs are not synonymous with idle residency. Default policy drains/checkpoints between model turns. When policy allows stronger cooperative preemption, preserve durable findings and requeue the current job with a new fenced attempt; do not kill unrelated active coding/test work. A late pre-eviction result must not overwrite a resumed attempt. Avoid starvation: priority for foreground testing must coexist with eventual scout progress when capacity returns. Record admission/queue/eviction reasons for diagnostics without adding GPU internals to ordinary question arguments.

Test idle eviction, active between-turn eviction, daemon restart, lost process, selective overlapping-island eviction, global foreground acquisition, cleanup completion and answer reuse after reload. No hard-coded GPU indices or mandatory new hardware. Hardware-gated acceptance uses the real installed runtime through supported reservation paths and leaves no benchmark jobs/reservations behind.
