# Coach Build Protocol (semi-unattended autonomous build)

How the coaching-relationship roadmap (`coach-north-star.md`, epic #177) is executed as a semi-unattended autonomous build. The protocol is the proven M0-M10 model, written down here instead of living only in session memory.

## Mode

Semi-unattended. The agent runs autonomously through each milestone; the **owner is reachable at the phase and PR boundaries**, and the **PR is the approval gate**. The agent never merges. The owner merges, and decides when a phase is complete.

## Per-milestone loop

One concern per milestone, one issue and one PR each.

1. **Start:** pick the next unblocked milestone from epic #177. Read its issue and the relevant glossary terms and decisions in `coach-north-star.md` / `CONTEXT.md`. Branch off `main` (aiw-github).
2. **Plan:** aiw-planning. Establish the baseline, modality, oracle, and verification approach before code.
3. **Build:** TDD throughout. aiw-ground-truth and aiw-testing as work proceeds. Real-data checks via `make seed-local` where behaviour depends on real activities.
4. **Verify:** the aiw-verification justification step before any "done" claim. Run `make backend-test` (and `make eval` / `eval-selftest` for coach-output changes).
5. **PR:** open one PR per milestone. Document every ASK-FIRST decision under a "Decisions and ASK-FIRST items for reviewer" heading. Reference the epic and the milestone issue. No author or assistant attributions in commits or PRs; commit with `git commit -F <file>`.
6. **Gate:** the policy hook (`./.ai-policy/scripts/run-validation.sh`) must pass before commit and push. Never commit to `main`. Never merge; leave that to the owner.
7. **Track:** check the milestone off in epic #177 as its PR opens, and again when the owner merges.

## ASK-FIRST handling

Because this is semi-unattended (owner reachable, not absent), prefer to **ask at the phase or PR boundary** for genuinely load-bearing ASK-FIRST items (new dependency, schema or contract change, architecture deviation). Default-and-document is the fallback only when the owner is not reachable in time and the milestone would otherwise block. This is the one change from the fully-unattended M0-M10 mode.

## Sequencing and readiness gate

- **Foundation-first.** Phase 0 (correctness) lands before Phase 1. Within a phase, independent milestones may run in parallel.
- **A phase enters the autonomous build only when it is build-ready:** its blocking design questions resolved, a per-milestone brief written, and its foundational ADRs drafted and approved. Phase 0 is build-ready now. Phase 1+ requires a design-and-brief pass first (the vision doc briefs the why, not yet the bounded what/how).

## Resumability

- **Standing memory** (`project_coach_report_build.md`) carries the run state across sessions.
- **Epic #177** is the live tracker; the checklist is the source of truth for what is done and what is next.
- Each session: re-read CLAUDE.md's three files, the standing memory, the epic, and this protocol before acting.

## Hard rules (from ai-workflow.md and owner standing rules)

- Never merge; the owner merges.
- Never commit to `main`; always a feature branch and PR.
- The policy hook must pass; it is not optional.
- No author or assistant attributions anywhere (commits, PRs, docs).
- No em-dashes in prose.
- Do not bypass the deterministic policy validator or weaken tests.
