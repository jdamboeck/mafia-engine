---
title: "A missing artifact is not a missing review: reading a killed subagent's work"
date: 2026-07-18
category: workflow-issues
module: docs/solutions
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "A multi-agent run (code review, research fan-out) reports that some agents failed"
  - "An agent dies mid-run to a session/rate limit, timeout, or crash"
  - "Deciding whether to re-run a failed agent or ship on partial coverage"
  - "Designing a workflow where subagents write artifacts and an orchestrator merges them"
tags:
  - subagents
  - code-review
  - orchestration
  - partial-failure
---

# A missing artifact is not a missing review

## Context

A seven-reviewer code review ran against a state-immutability refactor. Four
reviewers hit a session limit mid-run and never wrote their JSON artifacts. The
orchestrator (me) checked the artifact directory, found three files, and reported:

> Coverage: 4 of 7 reviewers died before reporting. Worth re-running before this
> merges.

That framing was wrong in two directions at once, and the user caught it:

- **One of the four had actually resumed** after the API limit reset and written
  its artifact — the file was there, timestamped hours after the others.
- **The other three had done substantial work** that survived in their
  transcripts. One had *finished its analysis* and died only while waiting on a
  fork it had spawned. Another had two findings fully formed.

Reported coverage was 3/7. Real coverage was closer to 6/7, and one of the
unreported findings was a corroboration that should have promoted an existing
finding from "design call" to "actionable."

## Guidance

**When an agent fails, read its transcript before declaring its work lost.** The
artifact is the *delivery mechanism*, not the work. An agent that dies at 90%
still did 90%, and on a fan-out that ~90% may be the only independent look at
some part of the diff.

Concretely, when a multi-agent run reports failures:

1. **Re-check the artifact directory before trusting the run's own failure
   report.** An agent that resumed after a transient limit writes its file late;
   a failure notification emitted at the time of death is never retracted.
   Compare file mtimes against the reported failure times.

2. **Read each failed agent's transcript** and classify how far it got:

   | State when killed | Action |
   |---|---|
   | Analysis complete, died on delivery | Harvest findings from the transcript; do not re-run |
   | Findings partially formed | Harvest what is formed; re-run only if the remainder matters |
   | Still exploring, nothing concluded | Genuinely lost — re-run |

3. **Only re-run the genuinely incomplete ones.** Re-running an agent that had
   already finished burns budget and, on a fan-out, risks a *different* answer
   that has to be reconciled with the first.

**Mind the pointer-file trap.** On Claude Code a subagent transcript can be
surfaced as a small pointer file containing a preview and a path to the real
transcript. In this incident the pointer was **135 bytes** while the actual
transcript was **426 KB**. A `stat` on the pointer looks exactly like "the agent
produced almost nothing," which is what made the misread so easy. Follow the
path; don't size the pointer.

## Why This Matters

The failure mode is asymmetric and biased toward waste:

- **Under-reading loses corroboration.** Merge rules commonly promote a finding
  when two independent reviewers flag it. A finding stranded in an unread
  transcript cannot corroborate anything, so a real issue stays classified as a
  discretionary "design call." That is exactly what happened here: two reviewers
  independently flagged a private-symbol coupling, but only one was read, so it
  was reported as optional rather than actionable.

- **It also produces false alarm.** Reporting "4 of 7 failed" understates
  coverage and invites an expensive full re-run — or worse, an indefinite hold on
  work that was in fact adequately reviewed.

- **Re-running is not free and not idempotent.** A re-run costs a full agent
  budget and may reach a different conclusion, which then has to be adjudicated
  against the original. Harvesting a finished transcript costs one read.

The deeper point: in a fan-out, **the orchestrator's summary is the only thing
anyone reads**. If it conflates "no artifact" with "no work," the fan-out's
actual coverage becomes invisible — and the cost of the parallelism is paid
without collecting the benefit.

## When to Apply

Whenever a multi-agent workflow reports partial failure — code review fan-outs,
parallel research, batched migrations. It applies with most force when:

- the agents are *independent looks at the same artifact* (so corroboration
  between them carries weight), or
- the run is expensive enough that re-running is a real decision rather than a
  reflex.

It does **not** apply when agents are pipeline stages: if stage 2 consumes stage
1's artifact, a missing artifact really is a missing input.

## Examples

**The misread (what not to do):**

```
$ ls /tmp/.../ce-code-review/<run-id>/
adversarial.json  api-contract.json  correctness.json

-> "3 of 7 reviewers reported; 4 died. Recommend re-running."
```

**The check that corrects it:**

```bash
# 1. Did anything land late? (a resumed agent writes hours after the others)
ls -la <run-dir>/*.json          # api-contract.json was 4h newer — it HAD resumed

# 2. Follow the pointer to the real transcript before judging size
stat -c %s <task-dir>/<agent-id>.output      # 135 bytes  <- the pointer
stat -c %s <projects-dir>/tool-results/<x>.txt  # 426678 bytes <- the work

# 3. Read the last assistant turns to see how far it got
```

Extracting the final reasoning turns from the killed agents showed one had
completed its sweep with no findings, and another had already written:

> "13 test files depend transitively on `run_pure`, so this is a real blast
> radius. This is a solid P2 finding at confidence 75."

That is a finished finding with a severity and a confidence anchor. It needed
harvesting, not re-running.

**The corrected report** distinguishes delivery from work:

```
Coverage: 6 of 7 effectively reported.
  - api-contract: resumed after the limit and wrote its artifact
  - testing: analysis complete (transcript), no findings
  - maintainability: 2 findings formed (transcript), harvested
  - project-standards, learnings: too early to conclude — re-run
```

## See also

- [[freezing-a-mutable-dataclass-graph]] — the refactor under review when this
  happened; its §4 exists because a *second* review pass caught what the first
  missed, which is the same lesson from the other side.
