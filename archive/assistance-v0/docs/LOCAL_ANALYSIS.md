# Durable local observer analysis

Use observer or mutator `investigate` for read-only local questions, with optional
registered project scope, retained hint aliases and explicit retry-safe request
IDs. Poll using the returned job ID instead of resubmitting. Accepted/pending
means the service-private broker durably retained the request; continue other
useful work and poll later. Capacity/provider failures are explicit.

Investigation and observer `skill` share one broker, durable observations,
attempt fencing, eviction retry, packet outbox and restart polling. Model/GPU
residency is independent of persistence. Polling needs no GPU. Startup does not
load a model or inject overview; runtime policy owns model, topology and resource
selection, not ordinary caller parameters.

The local worker has shared information tools and sandboxed read-only command/log;
it cannot recursively investigate, claim work, edit sources, mutate Todo or use
coding delegation. Project Control brokers allowed access and source provenance;
the Skills supervisor and resource interlock own inference and preemption.
Skill-mode workers read installed SKILL.md and follow authored maps/references.

Use `machine` for bounded current host facts, not benchmark launching. Source
mentions and previous answers are attributed evidence, not fresh verification.
Explicit coverage reports unavailable, stale, omitted or unresolved material.
See [surface examples](as1-surface.md), [job ports](as1-jobs.md), and
[skill navigation](SKILLS.md). This guide does not establish genuine local-model
reasoning quality, GPU qualification, or live release acceptance.
