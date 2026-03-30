# AI Workflow

Version: 1.0.0

This file defines the workflow for AI-assisted coding on this project.
It is written for the AI coding agent.
The human reviews and approves at defined checkpoints.

## First Principles

- The codebase provides implementation truth.
- Runtime behaviour is the final source of truth.

## Workflow

1. **Confirm the task and inputs.**

   - Confirm the GitHub issue number.
   - Read the issue and its comments.
   - Check parent and sub-issue structure.
   - See [Handling Parent and Sub-Issues](#handling-parent-and-sub-issues).
   - Check branch state.
   - See [GitHub Workflow](#github-workflow).
   - Confirm the task is a bounded change.
   - See [Scope Control](#scope-control).
   - Complete this step before analysing implementation details.

2. **Review project context.**

   - Read `project-spec.md` and relevant files.
   - Review the code areas the task is likely to touch.
   - Extract the intended outcome from the issue before using implementation suggestions.
   - See [Planning Requirements](#planning-requirements).

3. **Produce a code-aware plan.**

   - Write the plan.
   - See [Planning Requirements](#planning-requirements).

4. **Checkpoint: human reviews the plan.**

   - Update the plan if the human requests changes.

5. **Implement the approved scope.**

   - Implement the work defined in the approved plan.
   - See [Implementation Rules](#implementation-rules).
   - See [Scope Control](#scope-control).

6. **Run validation.**

   - Run validation checks.
   - See [Validation Requirements](#validation-requirements).

7. **Support manual verification.**

   - Suggest manual checks for the human.
   - See [Manual Verification Requirements](#manual-verification-requirements).

8. **Checkpoint: human reviews validation results and manual verification.**

9. **Fix and revalidate.**

   - Fix reported issues.
   - Rerun relevant validation checks after each fix.
   - Repeat until no issues remain.
   - Enter [Failure Analysis Mode](#failure-analysis-mode) if a fix fails.
   - Enter [Failure Analysis Mode](#failure-analysis-mode) if manual verification fails.


10. **Summarise and prepare handoff.**

   - Report what changed.
   - Report what was tested.
   - Report what was not tested.
   - Report remaining risks and follow-up work.
   - Check parent and sub-issue closure status.
   - See [Handling Parent and Sub-Issues](#handling-parent-and-sub-issues).
   - State which GitHub action would be next if the human wants to publish the work.
   - See [GitHub Workflow](#github-workflow).

11. **Checkpoint: human approves the next GitHub action.**

   - Stop after the summary until the human explicitly approves the next GitHub action in the current session.

12. **Run the approved GitHub action and stop.**

   - Run only the single GitHub action the human explicitly approved.
   - See [GitHub Workflow](#github-workflow).

## Planning Requirements

Before implementation, produce a plan that includes:

- State the goal of the change in one or two sentences.
- State the user-visible behaviour that must change.
- State the files and code areas the change will touch.
- State the proposed implementation approach.
- State any assumptions the plan depends on.
- Separate issue assumptions from codebase-confirmed assumptions.
- State how each critical assumption will be verified.
- State any remaining uncertainties or ambiguities.
- State the risks and edge cases.
- Mark the change as higher-risk if it affects routing, persistence, sync, caching, reactive subscriptions, or state transitions.
- Include at least one runtime validation step for higher-risk changes.
- State the validation approach.
- See [Validation Requirements](#validation-requirements).
- State any logging or observability changes needed.
- See [Logging and Observability](#logging-and-observability).

When writing the plan:

- Treat the issue goal as authoritative.
- Treat issue-suggested implementation details as provisional until the current codebase confirms them.
- Do not assume the files, data flow, or control points named in the issue are the real execution path.
- If the issue and the current codebase disagree, prioritise the codebase and flag the mismatch to the human.
- If the issue suggests a structure that the current codebase does not follow, plan against the real structure and flag the mismatch to the human.
- Keep the plan concise.

## Implementation Rules

During implementation:

- Use `project-spec.md` for initial context on architectural patterns, project structure, and conventions.
- Prefer extending current patterns over introducing new ones.
- Keep changes focused and relevant to the approved plan.

## Scope Control

Keep the change focused on the approved task:

- If the issue contains multiple unrelated objectives, flag this and ask the human whether to split them into separate tasks.
- If the task would require changes across many unrelated areas of the codebase, flag the risk and suggest decomposition.
- Do only the work required to complete the task.
- Do not treat "while I am here" changes as free.
- Separate fixes, refactors, and feature work unless the task clearly requires them together.
- If a larger problem is discovered, flag it as follow-up work instead of silently broadening the implementation.

## Validation Requirements

Run validation after every code change.
Run the following checks in order:

1. **Smoke tests.**

   - Confirm the app builds and starts without errors.

2. **Global test suite.**

   - Run the full existing test suite.

3. **Targeted tests.**

   - Run tests specific to the changed area.
   - If no targeted tests exist, flag this.

4. **New tests.**

   - Add tests if the change introduces behaviour that existing tests do not cover.
   - Run the new tests.

When running validation:

- Do not modify smoke tests or the global test suite unless the task explicitly requires it.
- If a previously passing test fails after the change, treat the change as wrong until proven otherwise.
- Run smoke tests and the global test suite after each meaningful implementation pass.
- Do not treat passing smoke tests and the global test suite as proof that the requested behaviour works.
- Treat existing passing tests as evidence of stability.
- Do not treat existing passing tests as proof of correctness for new behaviour.
- If the change affects state transitions, sync, routing, caching, or reactive UI updates, include validation that follows the full user path.
- If no automated test exercises the real user path, say so explicitly.
- If manual verification fails, stop implementation mode and enter [Failure Analysis Mode](#failure-analysis-mode) before making more code changes.

When reporting validation:

- Report what was tested and what passed.
- Report what failed and whether the failure is related to the change.
- Report what was not tested and why.
- Report failures honestly, including failures unrelated to the change.
- Do not claim code is tested when it is not.
- Do not ignore failing tests and continue as if the task is complete.

## Manual Verification Requirements

When supporting manual verification:

- Suggest specific manual checks based on the change.
- State the success signal for each check.
- State the failure signal for each check.

## Failure Analysis Mode

Enter failure analysis mode when manual verification fails.
Enter failure analysis mode when runtime behaviour contradicts the implementation.
Enter failure analysis mode when test results conflict with observed behaviour.

When in failure analysis mode:

- Stop making speculative fixes until the contradiction is described clearly.
- State the observed behaviour in user terms.
- State the expected behaviour in user terms.
- Restate the contradiction in one short block before any reasoning.
- Use this format: observed behaviour, expected behaviour, strongest conflicting evidence, and what remains unknown.
- List the assumptions the implementation relied on.
- Mark each assumption as verified, unverified, or disproved.
- List plausible failure causes across issue interpretation, code path selection, persistence, sync, caching, routing, UI binding, environment, test coverage, and observability.
- Identify the cheapest next observation that can eliminate one or more hypotheses.
- If multiple hypotheses exist, prefer the next observation that distinguishes between wrong code path, wrong write, later overwrite, sync overwrite, and stale runtime.
- Name the single leading hypothesis before proposing the next step.
- State the evidence that currently supports the leading hypothesis.
- If repeated fixes under different hypotheses are not converging, state this clearly.
- If investigation reveals that the plan was based on incorrect assumptions about the codebase, state this clearly.
- Report what the plan assumed.
- Report what the codebase actually does.
- Report what a revised approach would need to account for.
- If the contradiction involves a write, state transition, sync boundary, or reactive screen, prefer temporary diagnostics or direct observation over a retry request.
- Gather evidence before proposing another fix.
- Test at least one concrete hypothesis before asking the human to retry the flow, refresh the app, clear cache, restart the dev server, or repeat manual verification.
- Prioritise code path, persistence, sync, routing, and UI binding explanations over environment or caching unless evidence shows otherwise.
- Do not signal a flawed approach based on difficulty alone.
- Signal a flawed approach only when evidence shows the assumptions were wrong.

## Logging and Observability

Decide whether additional logging or observability is needed when the change introduces user-facing flows, data writes, sync operations, state transitions, silently failing error handling paths, or integration points between layers.

When adding logging:

- Use the project's existing logging approach.
- If no logging approach exists, flag this in the plan.
- Log the action, relevant identifiers, and outcome.
- Include enough context to trace a problem without a debugger.
- Do not add logging for trivial operations.
- Do not add logging that would expose sensitive user data.
- Do not log full data payloads unless explicitly needed.
- If a change affects writes, sync behaviour, state transitions, or reactive screens, decide whether temporary diagnostic logging is needed to verify the runtime path.
- Prefer logs or probes that confirm which code path executed.
- Prefer logs or probes that confirm which identifiers were used.
- Prefer logs or probes that confirm which state transition occurred.
- If observability is too weak to distinguish between competing hypotheses, flag this before continuing.
- Remove temporary diagnostics before completion unless the human approves keeping them.
- If a higher-risk change fails manual verification before the runtime path is proven, decide whether temporary diagnostics or another direct observation method is needed before asking the human to retry.

When investigating root causes or verifying runtime behaviour:

- Prefer automated diagnostics over asking the human to observe and report.
- Write diagnostics that capture structured, non-sensitive output.
- Capture event types, state changes, control flow decisions, and timestamps when relevant.
- Run the diagnostic.
- Read the results.
- Use the results to drive the next step.
- If a diagnostic cannot avoid capturing sensitive data, do not write it.
- Describe what to look for.
- Let the human observe it directly.
- Only ask the human to provide logs or reproduce behaviour when automated observation is not possible.
- If only the human can trigger the condition, ask the human to trigger it and provide the raw output.
- Do not ask the human to interpret the results.
- Do not write diagnostics that output sensitive data, including tokens, credentials, PII, full payloads, or anything that could identify a real user.

## Command Approval

When requesting approval to run a command:

- State what the command does.
- State why the command needs to run.

## GitHub Workflow

Every task must follow the GitHub branching workflow:

- Link every task to a GitHub issue before implementation.
- Do not work directly on `main`.
- Create a branch for the issue before starting work.
- If the branch name does not match the issue or task, stop and create or switch to an appropriate branch before continuing.
- Use the branch naming format `type/short-description`.
- Use `feature/` for new functionality.
- Use `fix/` for bug fixes.
- Use `refactor/` for refactors.
- Keep branch work focused on the issue scope.
- If the task changes significantly during implementation, update the issue or flag the mismatch to the human.
- Treat commit creation, push to remote, and pull request creation as separate GitHub actions.
- Do not infer approval for one GitHub action from approval for another GitHub action.
- Do not push to remote without explicit human confirmation in the current session.
- Do not create a pull request without explicit human confirmation in the current session.
- If new commits are added after approval, stop and ask again before the next remote GitHub action.
- After running an approved GitHub action, stop and report the result.

## Handling Parent and Sub-Issues

Some work may be organised into parent issues and sub-issues in GitHub.
When this structure is used:

- Treat a parent issue as broader context that may list sub-issues.
- Treat a sub-issue as a single bounded work item.
- If the provided issue is a parent issue with sub-issues, do not implement the full parent scope.
- If the provided issue is a parent issue with sub-issues, stop and ask which sub-issue to work on.
- If the provided issue is a sub-issue, read the parent issue and its comments for context.
- If the provided issue is a sub-issue, do not read further up the hierarchy.
- If the provided issue is a sub-issue, implement only the sub-issue scope.
- If the provided issue has no sub-issues, treat it as a standalone work item.
- When completing a sub-issue, check whether it is the last open sub-issue under the parent.
- If the completed sub-issue is the last open sub-issue under the parent, flag this to the human.

## Boundary Rules

### Always Do

The following apply to every task without exception:

- Follow the workflow steps in order.
- Stop and ask when anything is unclear, risky, or out of scope.
- Flag uncertainty, guessed behaviour, and incomplete validation explicitly.
- If two commands in a row do not reduce uncertainty, stop and ask the human before continuing.

### Ask First

Stop and ask the human before doing any of the following:

- Adding a new dependency.
- Changing architecture or established patterns.
- Changing database schema or sync-related behaviour.
- Changing public interfaces or shared contracts.
- Making broad refactors.
- Deleting files or removing significant code paths.
- Weakening, skipping, or removing tests.
- Introducing new conventions or changing existing ones.
- Introducing a new logging library or pattern.
- Making assumptions where the task or expected behaviour is unclear.
- Proceeding when the work conflicts with the current codebase or project constraints.

### Never Do

Do not do any of the following under any circumstances:

- Invent requirements not present in the task or project context.
- Silently expand scope or introduce unrelated changes.
- Claim the issue is nearly complete while the root cause is still unknown.
- Hardcode sensitive values.

## The Human is Responsible For

The AI cannot perform these tasks.
They must be completed by the human.

- Define and scope the task before it reaches the AI.
- Decompose larger work into sub-issues and provide a sub-issue rather than the parent as the starting input.
- Provide a well-formed GitHub issue as the starting input.
- Ensure the right project context is available.
- Review and approve the plan before coding starts.
- Monitor for scope drift during implementation.
- Perform manual verification where automated tests are not sufficient.
- Report issues found during review or testing.
- Decide when the work is complete.
