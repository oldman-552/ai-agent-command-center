# GitHub workflows

## Local development plane (always on)

- Each project has a repository in its workspace (`<WORKSPACES_DIR>/<org>/<project>/repo`), created on first use for
  new projects or cloned from the linked GitHub repository for existing ones (**Projects → Repository map →
  Import**).
- Each task gets its own **git worktree** on `agent/<agent-identifier>/task-<id>`. Agent tools (`files.write`,
  `repo.read`, `git.branch`, `git.commit`) operate only inside it; paths are confined (no `..`, no `.git`, no symlink
  escape); sandboxed tests run against exactly that worktree.
- Protected branches (repository rules, `main`, `master`, the default branch) can never be the target of an agent
  write. Overlaps between worktrees and textual conflicts with the base branch are tracked and shown.
- git runs hardened: hooks disabled, no fsmonitor, no external diff/textconv/filters, scrubbed environment.

## GitHub (when a repository is connected)

1. **Connect** under **GitHub**: repository `owner/name`, a fine-grained token (stored encrypted, write-only), optional
   webhook secret, optional API base for GitHub Enterprise.
2. **Link** the repository to a project.
3. **Pull requests**: when a reviewed task has commits, the platform pushes the task branch (token via askpass, never
   in URLs or argv) and opens a PR whose description lists the task, acceptance criteria with their evaluation
   results, the sandbox test run, the evaluation decision, changed files, overlaps/conflicts and a mock warning when
   applicable.
4. **Merge**: only through the Approval Center (`git.merge_protected`). The approval freezes the head commit SHA; the
   merge (GitHub merge API with `sha=`, or a local `--no-ff` merge) is refused if the branch moved, conflicts appeared
   or the base branch changed.
5. **Webhooks** `POST /api/github/webhook/{repository_id}`: HMAC `X-Hub-Signature-256` verification, replay protection
   by `X-GitHub-Delivery`; `pull_request` updates PR state, `check_run`/`status` attach CI results to the PR.

## Verification status

Tested against an in-memory GitHub HTTP fake and a real local bare repository (push, branches, merges, webhooks).
**Not yet run against a real github.com repository** with the owner's token (FI-06).
