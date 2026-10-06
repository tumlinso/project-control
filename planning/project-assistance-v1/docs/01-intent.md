# 1. Purpose and product shape

## What should become easier

The user should be able to move between detailed engineering and the larger research direction without repeatedly rebuilding context. External agents should discover relevant source, prior experiments, current constraints, and useful questions quickly. Spare local compute should turn selected uncertainties into information that changes a decision—not a growing pile of prose.

Project Control should help answer four practical questions: **What matters here? What do we actually know? What is the cheapest useful next check? How does this serve the project?** This is the organizing purpose for both background work and live answers.

The desired product is a smarter agentic coding CLI and repository oracle with continuity. It is not a replacement for a capable coding agent, a second Todo system, or a collection of permanently resident model personalities. The two slots execute the same weights. Roles are lightweight behavior presets and context selections; authority is an independent, enforced property.

## Recommended first release

Start with three behaviors: **gather**, **reason**, and **experiment**. A review is usually a reason task with an adversarial objective; a historian is a gather task about a change; a mechanism hunter is an experiment objective. The conversational UI is an entry point to the same runtime, not a fourth always-running brain. These names are internal vocabulary, not new MCP tools.

The first visible capability is a bounded focus window. The user identifies a project/subsystem and a goal. During actual source activity, Project Control prepares context or a useful check. An incoming agent receives that material through existing discovery and evidence paths. A relevant result contains exact references, what is known, what remains uncertain, and at most a small actionable suggestion. When the source changes, the result is refreshed or labeled stale rather than repeated as fact.

The second capability is suspended work. An inquiry that needs a child investigation, user answer, or experiment becomes a saved continuation. It consumes neither a model slot nor a GPU lease while waiting. The controller resumes it from explicit state when an exact dependency result is available.

The third capability is an isolated laboratory. With the applicable user grant, the assistant can write temporary tests and implementations, compile and measure them, and return results or candidate patches. It cannot silently edit, commit, publish, or complete the real project. A failed or unpromoted experiment can be valuable reusable knowledge.

## Keep the long-term direction explicit

Use a small user-owned goal card, linked to existing canonical project goals and decisions where available. It records the desired capability or scientific question, current focus, non-goals, success evidence, and when the goal should be reconsidered. The assistant may suggest a correction, but cannot silently replace the user's goals with an inferred roadmap.

Cellerator's current README is an example of useful intent: reusable biological organization should shape computation, with structure and values having different lifetimes and complete comparisons deciding whether a prepared approach wins [S25]. A useful assistant might notice that a kernel optimization improves steady-state time but loses once preparation is counted. It should then surface the break-even question and the relevant measurement—not obstruct the experiment because it looks different from an old plan.

Long-term guidance should appear at decision points: choosing a mechanism, interpreting an experiment, handing off work, or explaining a relevant divergence. It should not be a boilerplate admonition in every reply. User redirection always wins over an old focus card.

## Where this is worth using

This is especially useful for moving code, recurring source questions, expensive investigation that several agents duplicate, performance experiments with reusable results, and cross-workspace relationships with actual supporting evidence. It is also useful when a stalled decision can be resolved by a small test rather than more deliberation.

It is less useful for trivial reads an agent can perform directly, untouched repositories already adequately described, topics with no actionable uncertainty, or speculative improvements without a cost/benefit test. Not every new file needs an LLM summary. Not every question needs delegation. The correct idle action is often to do nothing.

## What not to build in v1

Do not introduce a new distributed graph database, mandatory vector store, external agent framework, learned scheduler, profile zoo, autonomous canonical backlog writer, or permanent background reviewer of every edit. Do not require native KV-cache save/restore, instruction-level reasoning snapshots, multiple model families, cloud fallback, or a custom GPU scheduler.

Do not expand the MCP surface to mirror internal jobs. Preserve the existing observer/coder/mutator contracts, exact-query semantics, and source access. A native coder can retrieve prepared context through its current tools; adding fresh investigator access to its MCP profile is a separate user decision, not a hidden consequence of this project [S03, S23].

## Product acceptance principle

A feature earns its place when it improves a representative workflow without disproportionate latency, token, power, storage, notification, or maintenance costs. Judge correctness and usefulness before style. A source-backed partial can succeed; misleading freshness or fabricated evidence cannot. Count ideas acted on or experiments that resolved uncertainty, not the number of suggestions generated.
