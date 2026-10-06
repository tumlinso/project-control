# 8. Decisions, defaults, and deliberate omissions

## Recommended architecture decisions

Use the existing broker and inference owner, with explicit resumable continuations. Keep the semantic graph for knowledge and a small typed dependency relation for execution readiness. Keep Todo authority separate from assistant-owned derived evidence. Prefer a few behavior presets over permanent profiles. Preserve MCP by default. Move the owned execution runtime into Project Control while retaining independent domain skills and host/workflow authority.

These are the recommended design, not claims that implementation is already present. The root can choose simpler module boundaries or reuse a newer equivalent mechanism after checking the active source. Material departures should be explained in the decision log, not hidden in a rewrite.

## Ask the user at the appropriate boundary

The machine decision register names the triggering boundary and default for each question. The implementing agent should ask when an answer changes product behavior, authority, retention or resource use. It should not block safe independent work while waiting.

**Initial focus and automatic work.** Which workspaces and goal cards should be active? Recommend demand-only at installation and explicit time-bounded focus windows. Do not infer persistent permission from this design discussion.

**Scratch deployment boundaries.** Coding and running scratch tests are already part of the requested product intent; do not ask whether the capability is wanted again. Establish which focused projects, toolchains, CPU limits and GPU windows the initial grant covers. Once that bounded grant is active, routine permitted scratch tests need no per-test confirmation. Keep execution off during initial unqualified rollout; network access stays disabled unless narrowly approved.

**Notifications and guidance.** Should discoveries appear only on request/handoff, or can selected urgent findings interrupt? Recommend a small digest and relevant context in answers; no unsolicited repeated warnings. Define what “urgent” means with the user before enabling interruption.

**Retention.** How long should useful notes, experiment artifacts and conversational summaries persist, and what storage ceiling is appropriate? Recommend bounded assistant-owned retention, deduplicated supporting evidence, and no hidden reasoning archive. This package does not choose a private-history retention policy for the user.

**Coder MCP access.** Current coder has twelve tools and no `investigate`; observer has it [S03, S23]. Prepared context can improve coder's existing reads without changing that. A local CLI can offer explicit fresh assistance. Add investigator access or reactivate delegation only if the user wants that narrow capability and its authority tests pass.

**Deployment and full runtime scope.** The recommended owner is Project Control, but old native coding harnesses have consumers and different contracts. Confirm any consequential removal, reactivation, or broad migration discovered by the consumer inventory. Default to preserving compatibility and dormant behavior, not migrating every historical backend or enabling write agents.

**Interactive latency/power envelope.** Record an acceptable interruption delay, default residency behavior, and automatic work budget. Recommended initial automation uses no more than one inference slot; explicit demand may use both. Physical capacity remains two same-weight slots.

## What the root can decide without interruption

Concrete filenames, ordinary helper functions, which existing test fixtures to extend, batched versus sequential bounded reads, small internal indexes, exact serial implementation order within an outcome, and conservative reversible tuning values do not require a user design meeting. Keep evidence and report meaningful choices at outcome boundaries.

## Not part of the initial commitment

Native KV save/restore, hidden-reasoning persistence, multiple model weights, background self-improvement of production prompts, learned attention ranking, broad repository summarization, proactive canonical Todo creation, reactivation of old delegate tools, a new MCP task protocol, cloud fallback, remote multi-user tenancy and a graphical control dashboard are deferred. They are not forbidden forever; each needs a demonstrated gap and an explicit scope decision.

Background experimentation is also not permission to change biology, project goals, model parameters, datasets or production files automatically. The assistant can suggest a novel mechanism and test it in a permitted laboratory. Promoting it remains a human/authorized-task decision.

## Priority when constraints conflict

Authority and truthful evidence outrank speed. User focus and current intent outrank inferred goals. Foreground demand outranks opportunistic work. Verified GPU release outranks retaining warm weights. A useful small result outranks exhaustive peripheral coverage. Simpler reuse outranks a new elegant abstraction with no demonstrated benefit.

The north star remains: help the user and external agents do better, more creative work while spending less effort rediscovering context and losing less of the project's long-term direction.
