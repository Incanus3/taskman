# Foundation Documentation Update Implementation Plan

**Goal:** Align the project documentation with the generated Phoenix application and document the standard optional PostgreSQL startup helper.

**Approach:** Replace the generated README with a Taskman-focused local setup entry point. Update the roadmap and handoff to distinguish completed Phoenix scaffolding from the remaining foundation verification work. Preserve the user-provided PostgreSQL helper exactly as it is.

**Scope constraints:** Do not change run_postgres.sh. Do not claim the foundation is complete until the database, test, formatting, and server workflows have been verified.

### Task 0: Place the path rule in global guidance

**Files:**

- Modify: /home/jakub/.codex/AGENTS.md
- Modify: AGENTS.md

**Produces:** A global instruction that keeps agent-internal names out of project documentation paths, with no duplicate Taskman-specific instruction.

- [x] Add the global documentation-path rule to /home/jakub/.codex/AGENTS.md and remove the accidental Documentation section from this repository's AGENTS.md.

---

### Task 1: Establish the Taskman local setup entry point

**Files:**

- Modify: README.md

**Consumes:** run_postgres.sh, which starts taskman_postgres on port 5432 with the default PostgreSQL user and password.

**Produces:** A concise, Taskman-specific README that directs a new local developer to start PostgreSQL with ./run_postgres.sh, run mix setup, and start Phoenix with mix phx.server.

- [x] Replace the generated Phoenix boilerplate with a Taskman introduction, a three-step local setup sequence (./run_postgres.sh, mix setup, mix phx.server), and a Development section that runs mix precommit. Keep the generated Phoenix Learn more links at the end. State that the helper is the standard path but optional when a compatible PostgreSQL instance is already running on localhost:5432.

- [x] Verify the README includes the standard commands:

~~~
rg -n '^# Taskman$|\./run_postgres\.sh|mix setup|mix phx\.server|mix precommit' README.md
~~~

Expected: one heading and all four commands are found.

### Task 2: Record the actual foundation stage

**Files:**

- Modify: docs/planning/roadmap.md
- Modify: docs/handoffs/implementation.md

**Consumes:** The generated Phoenix skeleton, the stock root route in lib/taskman_web/router.ex, and the local PostgreSQL helper.

**Produces:** Planning and handoff documents that agree the skeleton exists, foundation work is in progress, and Projects and basic Tasks have not begun.

- [x] Update the roadmap metadata to **Status:** Foundation in progress and **Updated:** 2026-07-18. Under Application foundation, state that the Phoenix LiveView skeleton exists and run_postgres.sh is the standard PostgreSQL startup path. State that database connectivity, the normal mix setup / mix precommit workflow, and an application boot smoke test remain to be verified before closing the slice.

- [x] Replace the handoff Current position and Next step sections with the actual state: the Phoenix skeleton is present, only the stock root page is implemented, Projects and basic Tasks have not begun, and the next work is foundation verification before refining the first product slice.

- [x] Verify that no stale pre-scaffold claim remains:

~~~
rg -n -i 'no implementation has started|will be generated|when implementation begins' README.md docs/planning/roadmap.md docs/handoffs/implementation.md
~~~

Expected: no matches.

### Task 3: Validate the documentation update

**Files:**

- Verify: README.md
- Verify: docs/planning/roadmap.md
- Verify: docs/handoffs/implementation.md
- Verify unchanged: run_postgres.sh

**Consumes:** The documentation changes from Tasks 1 and 2.

**Produces:** Evidence that the documents are internally consistent and the user-provided helper was not modified.

- [x] Inspect the documentation diff and workspace state:

~~~
but diff
but status --format json
~~~

Expected: the README, roadmap, and handoff reflect the foundation stage; run_postgres.sh remains an unmodified user-owned added file.

- [x] Confirm that the repository-relative Markdown links in the changed documents resolve to existing files.
