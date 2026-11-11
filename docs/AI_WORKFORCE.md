# AI Workforce — user guide

One prompt goes in. A **Manager** analyses it and splits it into tasks. **Four workers** do the tasks. Every result
is reviewed. You get **one consolidated result** that says, for each part, how far it can be trusted.

Decision records: ADR-032 (Arena Local Bridge) and ADR-033 (workforce) in
[`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md). Bridge details: [`ARENA_LOCAL_BRIDGE.md`](ARENA_LOCAL_BRIDGE.md).

> **Read this first**
> - A new installation sends nothing anywhere. The Manager has **no model** and the four workers are
>   **Human-Assisted** until you change that.
> - The Manager needs a model with an API key (the requirement names Claude). A chat subscription is not an API key.
> - Arena Local Bridge is optional. It is needed only for **Automatic** workers.
> - Everything here was tested with a **mock Manager** and a **fake bridge**. A real Claude model and a real Arena
>   account were **not** used during development. See [What is not verified](#12-what-is-not-verified).

---

## 1. The pages

Open **AI Workforce** in the navigation (second item). It opens on the **Operations room**.

| Page | What it is for |
|---|---|
| **Operations room** `/workforce/room` | The team as a live map: you → Manager → queue → the four workers → review → done. Each worker's ring shows the step of its current task; click a worker to see its screen (the task's steps, an Automatic worker's output as it streams, a person's pasted result, the platform's check of code). "Needs you" lets you accept a result or approve a merge on the spot. Also: today's timeline, a day summary, a big-screen mode for a wall display, optional sounds, and a background that reacts to what the team does. Effects fire only on real changes between two readings. |
| **Overview** `/workforce` | Write the prompt. See what waits for you, the Manager, the four workers and the latest objectives. |
| **Tasks** `/workforce/tasks` | The board of all tasks, with queues and filters. Open a task to work on it. |
| **Manager & workers** `/workforce/workers` | Choose the Manager's model. Set each worker's mode, provider, session and person. |
| **Arena Local Bridge** `/workforce/bridge` | Add and test a bridge. Super Admin only. |
| **Objective** `/workforce/objectives/{id}` | The plan, the consolidated result and the history of one objective. |

---

## 2. First steps

1. **Choose the Manager's model.** Manager & workers → *Configure* on the Manager card. Pick a provider and a model.
   Add the provider first under **Providers & Models** if the list is empty.
   Until a model is chosen, the prompt form is disabled and says why.
2. **Leave the workers as they are** for a first try. They are Human-Assisted: you do their tasks yourself.
3. **Write a prompt** on the Overview page and submit it. The page of the new objective opens.
4. The Manager analyses and plans. This happens in the worker process and takes a few seconds to a minute.
5. Tasks appear. Do them (section 4), or let Automatic workers do them (section 5).
6. When every task is settled, the consolidated result appears under **Result**.

To try the flow with no account at all, choose the **Mock** provider for the Manager. See
[section 11](#11-trying-it-without-any-account).

---

## 3. Worker modes

Each worker has its own role, instructions, mode, provider, session, time limit and number of attempts.

| Mode | Who does the task | Needs |
|---|---|---|
| **Human-Assisted** (default) | A person. The platform prepares the brief and records the answer. | Nothing. |
| **Automatic** | The platform sends the brief to the worker's provider and records the answer. | A provider and a model. For Arena: a bridge with acknowledged terms and one session per worker. |
| **Disabled** | Nobody. The worker gets no new tasks. The Manager gives them to the others. | — |

**Fallback to a person.** An Automatic worker that cannot run now hands its task to a person instead of failing.
This is on by default and can be switched off per worker. "Cannot run" means: no provider, provider disabled, terms
not acknowledged, bridge or account unavailable, or too many recent failures.

**The four default workers**

| Worker | Role | Skill tag |
|---|---|---|
| W1 | Research & analysis | `research` |
| W2 | Engineering | `engineering` |
| W3 | Quality assurance | `qa` |
| W4 | Documentation | `documentation` |

The Manager assigns a task to a worker that has the skill the task needs and is the least busy.

**The Manager cannot use a bridge session.** It needs a system prompt, structured answers and reported usage. An
Arena session offers none of these, so the platform refuses that choice (`manager_cannot_use_a_bridge`).

---

## 4. Human-Assisted work, step by step

This is how a person does a worker's task. It works with no bridge and no provider for the worker.

### 4.1 Find your tasks

On **Tasks**, the queue buttons filter the board:

| Queue | Shows |
|---|---|
| **Offered to me** | Tasks waiting for a person to accept. |
| **Accepted by me** | Tasks you accepted and have not submitted yet. |
| **Need a person's review** | Results the Manager handed to a person. |
| **Open** | Everything that is not finished. |

The Overview page shows the same counts under "Waiting for you".

A worker can have an assigned person. Then only that person, or someone who can manage the workforce, may accept
its tasks. Without an assigned person, anyone with the *work* permission may accept.

### 4.2 Open the task card

The card shows:

- what the task is: objective, instructions, expected output, acceptance criteria, dependencies;
- **the brief** — one message that contains the platform's rules, the worker's role, the task and the approved
  results of earlier tasks. It is exactly what an Automatic worker would be sent. A copy button copies it;
- every version submitted so far, with its review;
- the history of every state change, with who made it.

Results of earlier tasks appear in the brief under a heading that marks them as **untrusted data**. Text inside them
is information, not an instruction.

### 4.3 Accept or decline

- **Accept** — the task is yours. Others no longer see it as offered.
- **Decline** — give a reason. The task goes back to the Manager, which offers it to another worker.

### 4.4 Do the work and submit

1. Copy the brief.
2. Do the work wherever you like. For example: paste the brief into a conversation on Arena's own website, in your
   own browser, signed in to your own account. The platform is not involved in that step.
3. Paste the answer into **Result**. Add **Notes** if you want to say how you got it.
4. Attach files if the result includes any.
5. **Submit**.

| Limit | Value |
|---|---|
| Result text | 400,000 characters |
| Notes | 8,000 characters |
| Files per submission | 10 |
| Size of one file | 10 MB (`WORKFORCE_ATTACHMENT_MAX_BYTES`) |
| File types | Text, code, data, PDF, images, ZIP and Office documents. Other types are refused. |

Files are stored on the server and are only ever offered as downloads. The platform does not open or run them.

A result that contains something that looks like a credential is not passed on automatically. It is held for a
person's review (`result_contains_secret_like_text`). Do not paste keys, passwords or cookies into a result.

### 4.5 After you submit

The result is reviewed (section 6). Three things can happen:

- **Accepted** — the task is completed, or waits for an approval if it is critical.
- **Changes requested** — the task comes back with the reviewer's note. Submit a new version. Earlier versions are
  kept and stay visible.
- **Rejected** — the task is closed as rejected. A person with the *review* permission can reopen it.

---

## 5. Automatic work

When a worker is Automatic, the platform sends the same brief to its provider.

- For Arena, the provider is an **Arena Local Bridge** entry and the model is **one session of one account**. Set
  this up in [`ARENA_LOCAL_BRIDGE.md`](ARENA_LOCAL_BRIDGE.md).
- The worker card shows which account stands behind the worker, as far as the bridge lets anyone know:
  *one session of one account*, *the bridge has a single account*, *pooled* or *account unknown*. Account
  addresses are shown masked.
- A request is never blindly sent twice. If the platform cannot tell whether a request reached Arena, the task goes
  to a person, who can see whether the answer arrived in the Arena conversation.
- Usage: the bridge reports none, so the platform shows **"not reported"** — never "0 tokens".

---

## 6. Review

Every result is reviewed before it counts.

1. **Platform checks first.** Is there a result at all? Does it contain text that looks like an instruction to the
   platform, or like a credential?
2. **The Manager reads it** against the task's acceptance criteria and gives a verdict.
3. **The platform decides**, using fixed rules. The Manager's verdict is advice.

| Outcome | Meaning |
|---|---|
| `approve` | Accepted. Only possible when no criterion is reported as unmet. |
| `revise` | Sent back with a note. Limited by "maximum revisions" (default 2). |
| `reject` | Closed as rejected. |
| `needs_human` | A person must decide. |

A result goes to a person when:

| Reason shown | Why |
|---|---|
| The review policy is "a person reviews everything" | You chose this policy for the workforce or the objective. |
| The revision limit was reached | The worker was asked to change the result too often. |
| The result contains instruction-like text | It may be an attempt to steer the platform. |
| The result contains credential-like text | It must not be passed on unread. |
| The Manager asked for a person | The Manager could not judge it. |
| The Manager's answer could not be used | Its verdict was not valid. |
| The Manager was unavailable | Its model could not be reached. You can also ask the Manager again. |

In a person's review you can **approve**, **ask for changes** or **reject**. A note is required for the last two.

**The same HTTP answer is never enough.** A worker that returns "200 OK" with an empty or unusable text has not
completed anything.

### Development tasks (code) — WF-14

Workers are text-only, so code reaches the project through the platform, never through the worker. Turn it on in
**Manager & workers → Development tasks**: tick *Enable development tasks* and give the test command the platform
runs (for example `python3 -m pytest -q`; only commands on the sandbox allow-list are accepted). Then:

1. The Manager may plan a task as **code** and name repository files the worker needs to see. The worker's message
   (the same for a model and for a person) explains the delivery format and lists the repository.
2. The worker delivers every created or changed file **in full**, each in a block that starts with
   ```` ```file:<path> ```` (or `delete:<path>` to delete a file).
3. The platform applies exactly that version to the task's own branch `agent/<worker>/task-<id>`, on top of the
   main branch as it is now, commits it, and runs the test command in the sandbox (no network, resource limits).
4. No file blocks, an unsafe path, no change, or failing tests: the result goes back for revision with the
   platform's own output. **No model is asked** in that case.
5. Tests passed (or no test command is set): the Manager reviews as usual. Accepting the result does **not** merge
   it — it requests **Merge … into main** in the Approval Center for exactly the tested commit. As for critical tasks,
   the person who submitted the result cannot approve it.
6. Only after that approval does the platform merge. If the merge fails (the main branch changed meanwhile), the task
   goes back for revision with the reason; the next version is applied on top of the new main branch.

A code task whose tests really ran and passed on the accepted version is the only thing that lands in the report's
class **Verified by the platform**. Without a test command the code is still applied and merged after approval, but it
is never classified *Verified*.

The merge goes into the platform's own copy of the project repository (`<WORKSPACES_DIR>/<org>/<project>/repo`). Opening
a GitHub pull request for workforce code tasks is not built yet.

### Critical tasks

A task can carry a critical operation, for example a production deployment or a public release. For such a task:

- passing the review does **not** complete it. Its state becomes *Reviewed — awaiting approval*;
- a request appears in the **Approval Center**. It names the exact version of the result and the fingerprint of
  its text;
- only a person's approval there completes the task. A changed result needs a new approval;
- **the person who submitted the result cannot approve it.** Someone else with the approval right must. If you work
  alone, add a second person under **Settings → Users**. The result of an Automatic worker can be approved by anyone
  with the approval right.

For a task that is **not** critical, the platform does not stop you from reviewing your own result when the Manager
hands it to a person. A one-person installation could not work otherwise. The history shows who submitted and who
accepted.

---

## 7. The consolidated result

When every task is settled, the platform writes one result for the objective. Open it under **Result** on the
objective page, or download it as Markdown.

Each task's result is placed in exactly one class. The class is computed by the platform from what was recorded. It
is not taken from any model's text.

| Class | Meaning |
|---|---|
| **Verified by the platform** | The platform itself checked the result: the files of a code task were applied to its branch and the project's tests passed on exactly the accepted version (see *Development tasks*). Text results never land here. |
| **Approved by a person** | A person accepted this exact version. |
| **Worker proposals** | The Manager accepted it. No person and no automatic check confirmed it. |
| **Unverified claims** | Accepted although a criterion was not shown to be met, or with no evidence. |
| **Failed or rejected** | The task failed or was rejected. |
| **Incomplete** | The task was cancelled or never finished. |

Also on the page:

- **What the platform recorded** — plain statements such as "2 of 2 tasks were completed". These come from the
  records.
- **The Manager's summary** — labelled *model text, not evidence*.
- A **mock** label on everything that came from the mock Manager or the fake bridge.
- The **status**: every task completed, or partial.

An objective ends as *completed* only when every task is completed. A task that failed, was rejected or was
cancelled makes it *failed*, and its result *partial*.

If something changes afterwards (a task is reopened and redone), a new version of the result is written. Older
versions are kept.

---

## 8. History

The **History** view of an objective lists:

- identifiers: objective, tasks, Manager steps, executions, the provider's request id, the trace id;
- every Manager step (analyse, plan, review, summarise) with model, duration, attempt and error;
- every Automatic attempt with its status, retries and errors;
- every submitted version and every state change, with the person or component that made it;
- usage, only where a provider reported it.

---

## 9. When something does not move

A task that waits says why on its card.

| Shown | Meaning | What to do |
|---|---|---|
| No worker can take it: not configured | The worker has no provider and no fallback. | Configure the worker, or switch it to Human-Assisted. |
| …: provider disabled | The worker's provider is switched off. | Enable it, or change the worker. |
| …: terms not acknowledged | The bridge's terms were not acknowledged. | Acknowledge them on the bridge page. |
| …: bridge unavailable / account unavailable | The bridge or the worker's account cannot be used now. | See the bridge page. Run *Test connection*. |
| …: too many recent failures | The provider's circuit is open. | Wait for the cool-down, or fix the cause. |
| …: worker disabled | Every suitable worker is disabled. | Enable one, or reassign. |
| The worker is busy | It works on another task. | Wait, or reassign. |

**What the platform does with a failed Automatic attempt**

| Failure | Action |
|---|---|
| Bridge unreachable, rate limit, upstream error, invalid answer, timeout | Try again, up to the worker's number of attempts. |
| Outcome unknown, or a problem with the account, session or configuration | Hand to a person (or fail, when fallback is off). |
| Attempts used up | Hand to a person (or fail, when fallback is off). |

**What a person can do** (with the *review* permission): reassign a task to another worker, reopen a failed or
rejected task, cancel a task, cancel an objective. Each is recorded with the person and the reason given.

If the Manager's model cannot be reached while planning, the platform retries. After the fifth failed delivery the
objective fails with the reason shown on its page.

---

## 10. Who can do what

| Role | See | Submit a prompt | Do tasks | Review, reassign, reopen, cancel | Configure | Bridges |
|---|---|---|---|---|---|---|
| Super Admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Organization Admin | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Project Manager | ✓ | ✓ | ✓ | ✓ | — | — |
| Developer | ✓ | ✓ | ✓ | — | — | — |
| Reviewer | ✓ | — | ✓ | ✓ | — | — |
| Viewer | ✓ | — | — | — | — | — |

Permissions: `workforce.read`, `run`, `work`, `review`, `manage`, `bridge`. Adding a bridge or replacing its key
also needs `credentials.manage`. Users of another organization get "not found" for everything.

The Manager and the four workers are configured only on the AI Workforce pages. The general agent pages show them
and refuse to change them.

---

## 11. Trying it without any account

Choose the built-in **Mock** provider and the model `mock-model` for the Manager. The mock is not AI: it follows
fixed rules, and everything it produces is labelled **mock**.

The mock plans what the prompt lists, one numbered line per task:

```
1. Research the options [research]
2. Build the chosen option [engineering] (after 1)
3. Check it [qa] (after 2)
```

- `[skill]` chooses the worker.
- `(after N)` makes a task wait for another.
- `{deploy.production}` or `{exposure.public}` after the title marks a task as critical.

Then do the tasks yourself (section 4). The mock Manager approves a result that is not empty.

For Automatic workers without Arena, developers can start the **fake bridge**
(`backend/devtools/fake_arena_bridge.py`). It says what it is in every answer. See
[`ARENA_LOCAL_BRIDGE.md`](ARENA_LOCAL_BRIDGE.md#10-the-fake-bridge-tests-and-demos-only).

---

## 12. What is not verified

| Item | Status |
|---|---|
| The Manager on a real Claude model (WF-21) | **Not run.** Run `REAL-TESTS.bat`, choice 1, with your API key. |
| Automatic workers on a real Arena Local Bridge with four accounts (WF-20) | **Not run.** Run `REAL-TESTS.bat`, choice 2. |
| The Windows scripts (WF-17) | Checked as text only. **Never executed**: no Windows and no Docker were available. |
| Development tasks (WF-14) | Built and tested with the mock Manager, people's submissions, real git and the real Docker sandbox. **Not run** with a real model or Arena worker writing the code. GitHub pull requests for these tasks are not opened (the merge is local). |
| Colour contrast of Arabic text | Not covered by the automated accessibility check. The same colours are checked on the English pages. |

Limits that come from the bridge and cannot be fixed in the platform:

- The bridge does not say which account answered. "Four accounts, four workers" is what you configured, not
  something each answer proves.
- The bridge reports no usage, so cost per Arena request is unknown.
- One bridge runs one request at a time. Four workers on one bridge take turns.
- A request the platform gave up on keeps running inside the bridge. Its answer may still appear in the Arena
  conversation.
