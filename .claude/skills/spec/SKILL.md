---
name: spec
description: Create a feature specification file and its feature branch
argument-hint: Feature requirements
allowed-tools: Read, Write, Glob, Bash(git status:*), Bash(git branch:*), Bash(git switch:*)
disable-model-invocation: true
---

Create a feature specification file for this application based upon the user's requirements.
Always adhere to any rules or requirements set out in any CLAUDE.md files when responding.

Requirements: $ARGUMENTS

## Expected Output

- A human friendly feature title (feature_slug) in kebab-case (e.g. hr-employee-table)
- A valid git branch name not already being used (e.g. feature/hr-employee-table)
- A detailed markdown specification file in the \_specs/ folder (e.g. \_specs/hr-employee-table.md)

## Step 1. Validate the main branch

Unless otherwise instructed, abort, tell the user why, and do not continue if

- the current git branch is NOT the `main` branch
- or the `main` git branch has any uncommitted, unstaged, or untracked files

## Step 2. Parse the requirements

Based on the `$ARGUMENTS`, extract:

1. `feature_title`
   - A short, human readable title in Title Case.
   - Example: "Add Employee Creation Form".

2. `feature_slug`
   - A git safe slug.
   - Rules:
     - Lowercase
     - Kebab-case
     - Only `a-z`, `0-9` and `-`
     - Replace spaces and punctuation with `-`
     - Collapse multiple `-` into one
     - Trim `-` from start and end
     - Maximum length 40 characters
   - Example: `add-employee-creation-form`

3. `branch_name`
   - Format: `feature/<feature_slug>`
   - Example: `feature/add-employee-creation-form`.

If a sensible `feature_title` and `feature_slug` cannot be determined, ask the user to clarify instead of guessing.

## Step 3. Switch to the git feature branch

Before making any content, switch to the new git feature branch using the `branch_name`
derived in step 2. If the branch name is already in use, then abort, tell the user why,
and do not continue.

If the `git switch` command fails for any other reason (e.g. not a valid git branch name, detached HEAD, etc), then abort, tell the user the exact error, and do not continue.

## Step 4. Create the specification file

Before writing, use Glob to check whether `_specs/<feature_slug>.md` already exists. If it does, abort, tell the user the file already exists at that path, and do not continue.

Create a specification markdown document and save it in the \_specs folder using the `feature_slug` for the filename. Use the @\_templates/spec-template.md when creating the specification content. The
template must be followed exactly. Do not include any implementation, technical details, or code examples. If the template is missing, abort, tell the user why, and do not continue.

## Step 5. Final output to the user

After the file is saved, respond to the user with a short summary in this exact format:

Title: <feature_title>
Branch: <branch_name>
Spec file: \_specs/<feature_slug>.md

Do not repeat the full spec in the chat output unless the user explicitly asks to see it. The goal is to save the spec file and report where it lives and what branch name to use.

Step 5 is the last step of this skill. This skill produces one deliverable — the saved spec file (and the new git branch) — and nothing else. It is not a plan awaiting approval to implement. Once the summary above is printed, stop:

- Do not ask, in any wording, whether to proceed, implement, or execute the spec (e.g. "Would you like me to proceed?", "Should I start implementing this?", "Ready to execute — proceed?"). This applies in every mode, including Plan Mode.
- Do not call the `ExitPlanMode` tool from this skill, even if Plan Mode is active. Writing and saving the spec file is an explicit exception to Plan Mode's edit restrictions for this skill — it is the action this skill exists to perform, not a plan to be approved.
- Always automatically save the specification file regardless of mode.
- Do not take any further action or ask any follow-up question.
