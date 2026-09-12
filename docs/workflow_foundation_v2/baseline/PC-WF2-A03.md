# PC-WF2-A03 low-cost routing revalidation

Observed 2026-09-12 after A02.  This record reuses the bounded routing smoke
at `/home/tumlinson/wf2-preflight-evidence/2026-09-12-codex-routing-preflight.md`
and checks whether its configuration identity drifted before WF2 fan-out.
No new child was launched: the prior smoke already exercised the required
read-only question, and the rechecked routing files are unchanged.

## Current routing identity

The current configuration hashes equal the post-addendum hashes in the smoke
record:

| file | SHA-256 |
| --- | --- |
| `~/.codex/config.toml` | `80587faae7b985ff014046dfd8f8f8278b9203747d7ba973b490201d955ea21d` |
| `~/.codex/AGENTS.md` | `90d7c7dd7c23996f26c75396191e5be2bfb2b244a3508f954e3fcf752c74ec17` |
| `wf2-parallel-head.toml` | `98a91004725a74be9f88e60891c5cdbf7169b7f4575a01134c4f9e44e3f536e3` |

The role files remain at the smoke-recorded identities: scout is Luna-low;
researcher and implementer are Terra-medium; reviewer is Terra-high; and the
optional parallel head is Sol-medium.  The observed root model remains
Sol-medium; this task did not alter it.  `TERM=xterm-256color codex
--strict-config doctor` reported 20 checks passed, zero warnings, and zero
failures.  The effective configuration permits eight total session threads;
this is a ceiling, not a dispatch target.

## Facade and smoke evidence

`/home/tumlinson/codex-workspace` is the tracked facade at
`fd053f941b9fe62d1a3b86c209867b2f38183b16`.  Its `agents`, `rules`,
`config.toml`, `local.config.toml`, `AGENTS.md`, and `version.json` entries
resolve into `~/.codex`; its listed skill links resolve into `~/.codex/skills`.

The prior actual researcher launch was Terra-medium/read-only, directly used
Project Control `source_context`, and returned a bounded digest locating
readiness implementation.  The separate default-child and implementer smokes
confirmed Terra-medium routing; the latter made only `pwd` and `git status`.
All smoke trees remained depth one, with no source write, task claim, workflow
mutation, or descendant from the read-only smoke.  Because the configuration
hashes and facade identities above still match, this is accepted as current
routing evidence rather than re-running costly archaeology.

## Explicit limitation

Read-only researchers still receive visible workflow-mutation tool definitions
from the current Project Control profile.  Filesystem-read-only behavior and
instruction/policy prevented mutation in the smoke, but mechanical
observer-only workflow-tool isolation is not claimed.  That remains early
WF2 implementation work.
