# Research routing and evidence digests

This note supplements the repository's current [AGENTS.md](../../../AGENTS.md).
AGENTS.md governs workforce profiles, authority, and permissions; follow it if
guidance here conflicts. A role name or role file describes requested setup,
not proof of what a child session actually received.

Keep research bounded and independent. Use a scout for a known-path lookup, a
researcher for a separate question requiring deeper source or history review,
and a reviewer to independently check decisive evidence. An implementer owns
its assigned technical task. Context depth and difficulty describe the work;
they do not grant authority or select a model.

## Research digest

Return a concise digest that lets the parent assess the answer without
repeating the investigation. Include:

1. **Question and scope** — what was examined and excluded.
2. **Conclusion** — separate findings from recommendations and inference.
3. **Evidence** — repository and revision, exact paths and line references, or
   task/interface/evidence IDs sufficient to re-fetch decisive records.
4. **Conflicts and uncertainty** — stale or unverified observations, competing
   evidence, and effects on confidence. Do not treat an unchecked conflict as
   proof that none exists.
5. **Coverage and gaps** — sources or state examined, gaps, and whether the
   coverage supports the conclusion.
6. **Next action** — a bounded follow-up or a statement that none is needed.

Return raw records only when their fields matter. A digest is evidence, not an
authorization capability or workflow completion; workflow actions remain with
the authorized parent through the supported interface.

## Report observed execution accurately

Keep requested executor details separate from observed details. Report the
requested profile, model, and reasoning effort separately from the actual model,
effort, tools, sandbox, or approval state when those facts are available. If a
value was not observed, say so; do not infer it from a role name or setting.
When a claim depends on a child having restricted access, inspect its effective
access where possible. If that evidence is unavailable, say access was
unobserved and do not describe the session as isolated or read-only.

If the requested executor configuration is unavailable, report that limitation
to the parent. Do not silently retry with a more expensive model or broaden
permissions; leave any change in configuration to an authorized decision.
