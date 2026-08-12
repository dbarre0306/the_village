---
name: commit-message
description: Commit changes with commit message
allowed-tools: Bash(git status:*), Bash(git add:*), Bash(git diff --staged), Bash(git restore --staged:*), Bash(git commit:*)
disable-model-invocation: true
---

Commit changes with a git commit message based on the git changes: new, modified, and deleted files. If everything looks good, commit the changes.

Files to commit: $ARGUMENTS

## Commit classifications

Every git commit has a single classification. The valid classifications, in order of highest priority to lowest priority, are:

- New feature
- Bug fix
- Refactoring code
- Documentation
- Styling/formatting
- Tests
- Performance

You must determine the classification based on the git changes. Sometimes a commit may have more than one classification. If so, select the one with the highest priority.

## Classification emojis and types

- ✨ `feat:` - New feature
- 🐛 `fix:` - Bug fix
- 🔨 `refactor:` - Refactoring code
- 📝 `docs:` - Documentation
- 🎨 `style:` - Styling/formatting
- ✅ `test:` - Tests
- ⚡ `perf:` - Performance

## Steps when committing all changes:

If the list of files to commit ($ARGUMENTS) is empty, then attempt to commit all of the new, modified, and deleted files following these steps:

- Step 1: Use `git status` to determine if there is anything to commit. If not, then tell the user and stop (there is nothing more to do).
- Step 2: Filename secrets check (see "Secrets check" below, Phase 1) — scan the `git status` filenames for anything that commonly holds secrets. Exclude any matches from this commit and tell the user why. If nothing remains after exclusions, tell the user and stop.
- Step 3: Stage all of the remaining new, modified, and deleted files.
- Step 4: Get the git changes by using `git diff --staged`
- Step 5: Content secrets check (see "Secrets check" below, Phase 2) — re-scan the staged diff's actual content for anything that looks like a secret. If found, unstage just those files with `git restore --staged <file>`, exclude them, tell the user why, and re-run `git diff --staged` to get the corrected diff. If nothing remains after exclusions, tell the user and stop.
- Step 6: Analyze the git changes (the current staged diff) in order to create a concise one line description of the changes along with an optional longer description if necessary in order to provide details on what was changed and WHY IT WAS CHANGED. The longer description is only necessary when a one line description is insufficient. Use the present/imperative tense for the one line description and the optional longer description. If the changes seem to be unrelated, ask the user if they want to proceed, or want to exclude the unrelated files (list them). If the user chooses not to commit the unrelated files, then remove those files from this commit and unstage those files (`git restore --staged <file>`), then re-run `git diff --staged` and redraft the description from that corrected diff. If you fail to create a commit message, then abort (see below instructions).
- Step 7: Also use the git changes (the current staged diff) to determine the classification and its corresponding emoji and type.
- Step 8: Execute the commit.

## Steps when committing specific files:

If a list of files to commit ($ARGUMENTS) is given, then attempt to only commit those files following these steps:

- Step 1: Use `git status` to determine if the listed files have been created, modified, or deleted. If not, then abort (see below instructions).
- Step 2: Filename secrets check (see "Secrets check" below, Phase 1) — scan the listed filenames for anything that commonly holds secrets. Since the user explicitly named these files, don't auto-exclude a match: tell the user which file matched and why, and ask whether to proceed with it or exclude it. If every listed file ends up excluded, tell the user and stop.
- Step 3: Stage the remaining listed files.
- Step 4: Get the git changes by using `git diff --staged`
- Step 5: Content secrets check (see "Secrets check" below, Phase 2) — re-scan the staged diff's actual content for anything that looks like a secret. If found, unstage just those files with `git restore --staged <file>`, exclude them, tell the user why, and re-run `git diff --staged` to get the corrected diff. If nothing remains after exclusions, tell the user and stop.
- Step 6: Analyze the git changes (the current staged diff) in order to create a concise one line description of the changes along with an optional longer description if necessary in order to provide details on what was changed and WHY IT WAS CHANGED. The longer description is only necessary when a one line description is insufficient. Use the present/imperative tense for the one line description and the optional longer description. If you fail to create a commit message, then abort (see below instructions).
- Step 7: Also use the git changes (the current staged diff) to determine the classification and its corresponding emoji and type.
- Step 8: Execute the commit.

## Secrets check

This is a two-phase check.

- **Phase 1 (filename, before staging)**: scan candidate filenames for anything that commonly holds secrets — `.env` files, `*.pem`, `*credentials*`, `id_rsa`/`id_ed25519` (and other private key files), `*.key`, `*.p12`/`*.pfx`, `.npmrc`, `.pypirc`, `.netrc`, `service-account*.json`, etc.
  - When committing all changes: a match is auto-excluded (the user hasn't reviewed each file individually in this flow) — exclude it and tell the user why.
  - When committing specific named files: a match is not auto-excluded — the user already deliberately named this exact file, so ask them whether to proceed or exclude it, and tell them why it matched.
- **Phase 2 (content, after staging)**: filenames alone can't catch a secret embedded in an otherwise-ordinary file. Once files are staged, re-scan the actual `git diff --staged` content for anything that looks like an API key, token, password, or private key block. If found, unstage just that file (`git restore --staged <file>`) and exclude it — this phase always auto-excludes in both flows, since by this point the match is based on actual content, not just a filename guess.

In both phases, never silently commit an excluded/unresolved file — tell the user which file was affected and why.

## Safety rules

- Never use `git commit --no-verify` or `--no-gpg-sign`. If a pre-commit hook rejects the commit, fix the underlying issue, re-stage, and retry with a new commit — do not bypass the hook.
- Never use `git commit --amend`. Always create a new commit.
- Never force-add an ignored file (`git add -f`).

## Git Message Format:

Use the following format for making the commit message:

```
<emoji> <type>: <concise_description>
<optional_body_explaining_why>
```

## How to abort

If you need to abort — including when `git commit` itself fails and the underlying issue can't be resolved (see "Safety rules") — you must

- stop what you are doing
- undo any changes you made, e.g. unstage files you staged
- tell the user why you aborted
- do not continue

## Output:

1. Show the list of files that have been committed
2. Show the commit message along with its emoji
3. If any files were excluded along the way (secrets check or unrelated changes), list them and why
