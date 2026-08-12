---
name: build-plan
description: Build a feature plan file
argument-hint: Path or slug of a spec file under _specs/
allowed-tools: Read, Write, Glob
disable-model-invocation: true
---

You are helping to turn an existing feature specification into an implementation plan. Always adhere to any rules or requirements set out in any CLAUDE.md files when responding.

Specification File: $ARGUMENTS

## High level behavior

Your job is to turn the spec file referenced above into a detailed markdown implementation plan, saved under the `_plans/` directory using the same base name as the spec file. Do NOT implement the plan.

## Step 1. Resolve the spec file

`$ARGUMENTS` may be a full path (e.g. `_specs/add-employee-creation-form.md`) or just a slug (e.g. `add-employee-creation-form`). If it's not already a path ending in `.md`, use Glob to find a matching file under `_specs/`. If no spec file can be found, or more than one plausibly matches, stop and ask the user to clarify instead of guessing.

## Step 2. Read the spec

Read the resolved spec file in full. If it is missing required sections (e.g. no Acceptance Criteria, no Scope) such that a plan can't be produced responsibly, stop and tell the user what's missing instead of guessing at intent.

## Step 3. Draft the plan content

Use the @\_templates/plan-template.md when creating the plan content. The template must be followed exactly. If the template is missing, abort, tell the user why, and do not continue.

The plan should:

- Reference the spec file it was built from.
- Cover every Acceptance Criterion and respect the spec's In Scope / Out of Scope split.
- List concrete files to change or create, and ordered implementation steps.
- Not include actual code — describe changes, don't write them.

## Step 4. Save the plan

Before writing, use Glob to check whether `_plans/<feature-slug>.md` already exists. If it does, abort, tell the user the file already exists at that path, and do not continue.

Save the plan to `_plans/<feature-slug>.md`, using the same base name as the spec file (e.g. `_specs/card-component.md` → `_plans/card-component.md`).

## Step 5. Final output to the user

After the file is saved, respond with a short summary in this exact format:

Spec file: \_specs/<feature-slug>.md
Plan file: \_plans/<feature-slug>.md

Do not repeat the full plan in the chat output unless the user explicitly asks to see it.

Step 5 is the last step of this skill. This skill produces one deliverable — the saved plan file — and nothing else. It is not a request awaiting approval to implement. Once the summary above is printed, stop:

- Do not ask, in any wording, whether to proceed, implement, or execute the plan (e.g. "Would you like me to proceed?", "Should I start implementing this?", "Ready to execute — proceed?"). This applies in every mode, including Plan Mode.
- Do not call the `ExitPlanMode` tool from this skill, even if Plan Mode is active. Writing and saving the plan file is an explicit exception to Plan Mode's edit restrictions for this skill — it is the action this skill exists to perform, not a plan to be approved.
- Always automatically save the plan file regardless of mode.
- Do not take any further action or ask any follow-up question.
