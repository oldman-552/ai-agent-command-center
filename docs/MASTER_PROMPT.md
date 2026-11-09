# AI AGENT COMMAND CENTER
## MASTER SOFTWARE DEVELOPMENT PROMPT
### RCTFC FRAMEWORK — ROLE, CONTEXT, TASK, FORMAT, CONSTRAINTS

**Project:** AI Agent Command Center  
**Product Type:** Multi-Agent AI Software Development Management Platform  
**Interface Languages:** Arabic and English  
**Operating System:** Windows-first  
**Architecture:** Multi-Agent Orchestration + GitHub Integration  
**Mandatory UI/UX Skill:** UI UX Pro Max  
**Development Environment:** Claude Code

---

# R — ROLE

You are an elite software engineering team responsible for designing, developing, testing, and delivering a production-ready AI Multi-Agent Management Platform.

You must operate simultaneously as:

1. Principal Software Architect.
2. Senior Full-Stack Engineer.
3. AI Multi-Agent Systems Engineer.
4. Backend Engineer.
5. Frontend Engineer.
6. DevOps Engineer.
7. Database Architect.
8. UI/UX Designer.
9. Security Engineer.
10. QA Automation Engineer.
11. Technical Project Manager.

Act as a senior engineering team delivering a real, maintainable, scalable software product.

You are responsible for:

- Analyzing the existing development environment.
- Designing the architecture.
- Building the complete application.
- Writing actual production-quality code.
- Creating all required files.
- Installing and configuring dependencies.
- Running the application.
- Testing all major functionality.
- Fixing errors.
- Maintaining project documentation.
- Tracking actual development progress.

Do not merely describe what you intend to build.

You must implement the application, execute it, test it, and verify its functionality.

Never claim that a feature is complete unless it has actually been implemented and tested.

---

# C — CONTEXT

## 1. Project Overview

I want to build a platform called:

# AI Agent Command Center

This platform will function as a centralized management system for a team of AI agents that collaboratively develop software projects stored in GitHub.

I am the owner of the platform and the Super Administrator.

I want to submit a single development request through a web interface.

A central Manager Agent must analyze the request, create an implementation plan, distribute tasks to specialized AI agents, monitor their execution, collect their results, review their work, request corrections when necessary, and prepare changes for approval.

The system should operate like a real software development company with an AI management hierarchy.

## 2. My Existing Resources

I have:

- A GitHub account and repositories.
- A Claude subscription.
- Employees who have accounts on Arena AI Agent.

Arena AI URL:

https://arena.ai/agent

I want the platform to be extensible enough to support different AI providers, models, agents, and execution environments.

Important:

Do not assume that a personal Claude subscription automatically includes Anthropic API access.

Treat Claude Code authentication and Anthropic API authentication as separate integration methods with their respective limitations and billing models.

For Arena AI:

- Investigate its official documentation.
- Verify whether a supported API is available.
- Only implement an integration if an official, supported API exists and is accessible.
- Do not use unofficial browser automation, session extraction, credential scraping, or login bypass techniques.
- The platform must work independently of Arena AI.

## 3. Proposed Technology Stack

Use the following architecture:

### Frontend
- Next.js.
- React.
- TypeScript.
- Tailwind CSS.
- shadcn/ui.
- Internationalization support.

### Backend
- Python.
- FastAPI.
- LangGraph.
- Anthropic SDK.
- OpenAI SDK.

### Messaging
- RabbitMQ.

### Database
- PostgreSQL.
- SQLAlchemy.
- Alembic.

### Execution Environment
- Docker.
- Git.
- Git Worktrees.

### Deployment
- Docker Compose.

### Live Updates
- WebSockets or Server-Sent Events.

Use currently supported and compatible package versions.

Avoid deprecated libraries.

---

# T — TASK

Build the complete platform with the following functionality.

## T1 — Fully Bilingual Web Interface

This is a mandatory requirement.

The entire application must support:

- Arabic.
- English.

Arabic must be the default language on first launch.

Add a language switcher that is accessible throughout the application.

### Arabic Mode

When Arabic is selected:

- Enable proper RTL layout.
- Reverse layout direction where appropriate.
- Align navigation, tables, forms, dialogs, and components correctly.
- Ensure icons and directional elements are positioned correctly.
- Prevent text clipping and overlapping.
- Use appropriate Arabic fonts.

### English Mode

When English is selected:

- Enable proper LTR layout.
- Ensure all components use correct English alignment and direction.

### Internationalization Requirements

Use a proper i18n architecture.

Do not hardcode user-facing text inside React components.

Store translations in organized language files.

Translate all:

- Navigation labels.
- Page titles.
- Buttons.
- Forms.
- Validation messages.
- Notifications.
- Error messages.
- Status labels.
- Tooltips.
- Dialogs.
- Empty states.
- Loading states.
- System messages.
- Dashboard charts.
- Settings.
- Agent statuses.

Persist the user's selected language.

Test both RTL and LTR layouts.

The application must be fully usable in both languages, not partially translated.

---

## T2 — Dashboard

Create a professional SaaS dashboard.

Display:

- Total agents.
- Active agents.
- Available agents.
- Disabled agents.
- Failed agents.
- Running tasks.
- Completed tasks.
- Failed tasks.
- Tasks awaiting approval.
- Connected projects.
- Connected GitHub repositories.
- Token consumption.
- Estimated AI costs.
- Task success rate.
- Recent activities.
- Agent performance.
- Project progress.

Include professional charts and real-time updates.

Do not use hardcoded statistics in the production application.

---

## T3 — Agent Management System

This is one of the most important features.

I must be able to create and manage AI agents entirely through the web interface.

I must not need to modify source code whenever I want to add a new agent.

### Required Features

- Create agents.
- Edit agents.
- Delete or archive agents.
- Enable and disable agents.
- Start and stop agents.
- Duplicate agents.
- Change roles.
- Change specialties.
- Change AI providers.
- Change models.
- Edit system prompts.
- Assign tools.
- Assign permissions.
- Assign projects.
- Set priorities.
- Set execution limits.
- Set supervisors.

### Agent Creation Form

Include the following fields:

1. Agent Name.
2. Agent Identifier.
3. Role.
4. Specialty.
5. Description.
6. System Prompt.
7. AI Provider.
8. AI Model.
9. Model Configuration.
10. Allowed Tools.
11. GitHub Permissions.
12. Workspace Permissions.
13. Maximum Parallel Tasks.
14. Execution Timeout.
15. Token Budget.
16. Priority.
17. Supervisor Agent.
18. Activation Status.

Support creating a large number of agents, subject to available infrastructure and provider limits.

### Default Agent Roles

Create these initial roles:

- Manager Agent.
- Software Architect.
- Backend Developer.
- Frontend Developer.
- Database Engineer.
- DevOps Engineer.
- QA Tester.
- Security Reviewer.
- Code Reviewer.
- Research Agent.

Roles must be configurable and extensible.

Do not hardcode the system to only these roles.

---

## T4 — Agent Details

When opening an agent, display:

- Agent profile.
- Name.
- Description.
- Specialty.
- Current status.
- AI provider.
- Model.
- System prompt.
- Permissions.
- Available tools.
- Assigned projects.
- Current tasks.
- Previous tasks.
- Execution history.
- Conversation history.
- Errors.
- Token usage.
- Estimated cost.

Include a direct chat interface for communicating with individual agents.

Direct chat must never bypass the agent's assigned permissions.

---

## T5 — Central Manager Agent

Build a central Manager Agent.

This is the main intelligence and coordination layer of the platform.

The Manager Agent is responsible for:

1. Receiving development requests.
2. Understanding project requirements.
3. Inspecting the GitHub repository.
4. Analyzing the existing codebase.
5. Creating an implementation plan.
6. Breaking requests into smaller tasks.
7. Identifying task dependencies.
8. Selecting appropriate agents.
9. Assigning priorities.
10. Distributing tasks.
11. Coordinating parallel execution.
12. Monitoring agents.
13. Collecting execution results.
14. Reviewing code.
15. Detecting errors.
16. Requesting corrections.
17. Reassigning failed tasks.
18. Detecting merge conflicts.
19. Reviewing pull requests.
20. Preparing final reports.

### Manager Control Panel

Create a dedicated management interface.

I must be able to:

- Edit the Manager System Prompt.
- Enable or disable the Manager.
- Start or stop the Manager.
- Set its autonomy level.
- Set maximum parallel agents.
- Set budget limits.
- Configure priorities.
- Review its decisions.
- Review task assignments.
- Review recommendations.
- Approve or reject recommendations.

The Manager Agent may autonomously coordinate routine development activities.

However, it must never override my authority as Super Administrator.

Do not expose private model chain-of-thought.

Instead, provide concise operational explanations, task-routing reasons, decisions, and verifiable evidence.

---

## T6 — Task Management Center

Create a complete task management system.

Include:

- Kanban Board.
- Task List.
- Task Details.
- Task Dependencies.
- Agent Assignment.
- Project Assignment.
- Priority.
- Deadlines.
- Execution Status.
- Retry.
- Cancel.
- Pause.
- Resume.

Supported task states:

- Draft.
- Queued.
- Running.
- Blocked.
- Waiting Approval.
- Reviewing.
- Completed.
- Failed.
- Cancelled.

The Manager Agent must be able to break one development request into many tasks and distribute independent tasks in parallel.

---

## T7 — GitHub Integration

Create a complete GitHub integration page.

Use GitHub App where possible, or OAuth when appropriate.

Features:

- Connect GitHub.
- Display accessible repositories.
- Add projects.
- Select repositories.
- Select default branches.
- View branches.
- View commits.
- View pull requests.
- View changed files.
- View diffs.
- Configure agent access per project.

Each agent must work in an isolated workspace.

Use Git Worktrees or separate working environments.

Prevent agents from directly modifying the main branch.

Allow agents to create pull requests when their assigned tasks are completed.

Include meaningful pull request descriptions and test results.

---

## T8 — Approval Center

Create a dedicated approval center for the Super Administrator.

Approval must be required for:

- Merging changes into the main branch.
- Production deployment.
- Deleting important data.
- Changing security settings.
- Changing permissions.
- High-risk operations.

Provide these actions:

- Approve.
- Reject.
- Request Changes.

No protected operation may execute before explicit human approval.

Enforce approval rules in the backend, not just the frontend.

Record:

- Approver.
- Timestamp.
- Decision.
- Reason.
- Related task.
- Related pull request.

---

## T9 — RabbitMQ Messaging Infrastructure

Use RabbitMQ to distribute messages and tasks between agents.

Implement flexible queues and routing based on:

- Agent specialty.
- Task type.
- Priority.
- Project.
- Execution availability.

Support:

- Parallel execution.
- Task routing.
- Message acknowledgment.
- Retries.
- Dead-letter queues.
- Timeouts.
- Idempotency.
- Recovery after service restart.

---

## T10 — LangGraph Orchestration

Use LangGraph to manage the actual workflow and coordination logic.

The Manager Agent must use LangGraph for:

- Planning.
- Task decomposition.
- Dependency management.
- Parallel execution.
- Result collection.
- Review loops.
- Error recovery.
- Task reassignment.
- Workflow persistence.

RabbitMQ is responsible for message delivery and worker distribution.

LangGraph is responsible for orchestration and workflow decisions.

Do not confuse these responsibilities.

---

## T11 — AI Provider Management

Create a flexible provider architecture.

Initially support:

- Anthropic Claude API.
- OpenAI API.

Implement a unified provider adapter interface.

Allow additional providers to be added later.

Create a provider settings page containing:

- Provider Name.
- API Credentials.
- Available Models.
- Connection Status.
- Connection Test.
- Token Usage.
- Estimated Costs.
- Model Configuration.

Encrypt sensitive credentials.

Never expose complete API keys after saving them.

Do not assume a Claude subscription is equivalent to API access.

---

## T12 — Arena AI Integration

Make the architecture extensible for future Arena AI integration.

Investigate official Arena AI documentation.

If an official, supported API exists and is accessible:

- Build a separate Arena Provider Adapter.
- Document authentication.
- Test the integration.

If no suitable official API is available:

- Do not use unofficial browser automation.
- Do not scrape sessions.
- Do not store employee passwords.
- Create a documented extension point for future integration.

The platform must remain fully functional without Arena AI.

---

## T13 — Docker-Based Agent Execution

Agents must be able to perform actual software development work.

Depending on permissions, agents should be able to:

- Read project files.
- Create files.
- Modify files.
- Delete authorized files.
- Run tests.
- Run build commands.
- Execute Git commands.
- Create branches.
- Push changes.
- Create pull requests.

Use Docker to isolate execution environments.

Implement:

- CPU limits.
- Memory limits.
- Execution time limits.
- Filesystem restrictions.
- Workspace isolation.
- Secret isolation.
- Command execution policies.
- Execution logging.
- Secret redaction.

Never allow unrestricted agent execution on the host operating system.

---

## T14 — Role-Based Permissions

Implement a complete Roles and Permissions system.

Initial roles:

- Super Admin.
- Manager Agent.
- Developer Agent.
- Reviewer Agent.
- Read Only Agent.

I am the Super Administrator.

Agents must never be able to:

- Grant themselves additional permissions.
- Change system security policies.
- Override approval requirements.
- Merge into protected branches.
- Deploy to production without approval.

All permissions must be enforced by backend authorization policies.

---

## T15 — Activity Logs

Create a complete activity and audit log.

Record:

- User.
- Agent.
- Project.
- Task.
- Action.
- Timestamp.
- Changed files.
- Executed commands.
- Test results.
- Manager decisions.
- Approval decisions.
- Errors.
- Token consumption.
- Estimated cost.

Make the logs searchable and filterable.

Never expose passwords, API keys, or sensitive credentials in logs.

---

# WINDOWS AUTOMATION REQUIREMENTS

## T16 — START.bat

Create a Windows batch file in the project root:

`START.bat`

I want to start the entire application by double-clicking this file.

The launcher must:

1. Detect whether Docker is installed.
2. Check whether Docker Engine is running.
3. Verify required configuration files.
4. Create `.env` from `.env.example` when appropriate.
5. Never insert fake production credentials.
6. Build Docker images when necessary.
7. Start services using Docker Compose.
8. Wait for PostgreSQL readiness.
9. Wait for RabbitMQ readiness.
10. Wait for backend readiness.
11. Wait for frontend readiness.
12. Run database migrations safely.
13. Open the default browser automatically.
14. Display the application URL.
15. Display useful error messages.
16. Save execution logs in a logs directory.

If Docker is missing:

- Display a clear installation message.
- Provide the official Docker Desktop installation link.
- Do not automatically install software without permission.

The launcher must support Windows paths containing spaces and non-English characters.

Use correct quoting and `%~dp0`.

Do not delete database volumes or user data during normal startup.

## T17 — STOP.bat

Create:

`STOP.bat`

It must stop all application services safely.

It must not delete:

- Database volumes.
- Projects.
- Repositories.
- Persistent files.
- Application configuration.

## T18 — UPDATE.bat

Create an optional update script.

It should:

- Check local changes.
- Detect available updates.
- Preserve user data.
- Back up important data when necessary.
- Apply updates safely.

## T19 — RESET-DEV.bat

Create an optional development reset script.

It must display a serious warning and require explicit confirmation before deleting development data.

Never use it during normal application startup.

---

# PROJECT PROGRESS TRACKING

## T20 — progress.txt

Create a UTF-8 text file in the project root:

`progress.txt`

This file must show the actual development progress.

I want it updated automatically every five minutes while development work is actively running.

Also update it immediately when:

- A major phase is completed.
- A major milestone is reached.
- A critical error occurs.
- A blocker appears.
- A significant implementation decision is made.

### Required Contents

The file must contain:

1. Last Updated timestamp.
2. Overall completion percentage.
3. Current development phase.
4. Completion percentage of each phase.
5. Completed tasks.
6. Tasks in progress.
7. Pending tasks.
8. Successful tests.
9. Failed tests.
10. Current blockers.
11. Decisions requiring my approval.
12. Recently completed components.
13. Next implementation step.

### Progress Calculation

Divide the project into weighted phases totaling 100%.

Calculate overall completion based on verified implementation tasks and acceptance criteria.

Do not calculate progress based merely on:

- Time spent.
- Number of files created.
- Number of lines written.
- Subjective estimates.

A feature is complete only when its acceptance criteria are met and the relevant tests pass.

### Suggested File Format

```text
AI AGENT COMMAND CENTER
PROJECT DEVELOPMENT PROGRESS

Last Updated: YYYY-MM-DD HH:MM
Overall Completion: 00%

CURRENT PHASE:
Project Foundation

PHASE PROGRESS:

1. Project Foundation: 0%
2. UI UX Design System: 0%
3. Bilingual Frontend: 0%
4. Backend and Database: 0%
5. Agent Management: 0%
6. AI Providers: 0%
7. Manager Agent: 0%
8. LangGraph Orchestration: 0%
9. RabbitMQ Parallel Execution: 0%
10. GitHub Integration: 0%
11. Docker Sandbox: 0%
12. Approval and Security: 0%
13. Windows Launcher: 0%
14. Testing: 0%
15. Documentation and Release: 0%

COMPLETED TASKS:

IN PROGRESS:

PENDING TASKS:

SUCCESSFUL TESTS:

FAILED TESTS:

BLOCKERS:

REQUIRES ADMIN APPROVAL:

RECENTLY COMPLETED:

NEXT STEP:

UPDATE HISTORY:

- YYYY-MM-DD HH:MM — Initial project baseline — 0%
```

Keep historical updates rather than overwriting the entire history.

Update this file at least every five minutes during the active Claude Code development session.

Important:

Do not claim that progress will continue updating after Claude Code or the computer is shut down.

An optional background monitoring service may be implemented later, but do not create persistent Windows scheduled tasks or background services without my explicit permission.

---

# F — FORMAT

## T21 — Mandatory UI UX Pro Max Skill

Use this GitHub repository as the mandatory UI/UX skill reference:

https://github.com/nextlevelbuilder/ui-ux-pro-max-skill

Before implementing the frontend:

1. Inspect the official repository.
2. Read its README and installation instructions.
3. Install the skill using the officially documented method compatible with Claude Code.
4. Verify the installation.
5. Read the skill instructions.
6. Generate an appropriate design system.
7. Apply its design recommendations throughout the application.
8. Follow its UI/UX quality checklist.

Preferred CLI installation, if supported by the current official documentation:

```bash
npm install -g ui-ux-pro-max-cli
```

Then inside the project directory:

```bash
uipro init --ai claude
```

If the official Claude Code Marketplace method is more appropriate, use that method instead.

Do not install both methods unnecessarily.

Use UI UX Pro Max for:

- Design System.
- Color palettes.
- Typography.
- Dashboard layout.
- Navigation.
- Tables.
- Forms.
- Charts.
- Responsive layouts.
- Accessibility.
- UX patterns.
- Component design.
- Pre-delivery quality checks.

The UI must look like a professional commercial SaaS platform.

Do not simply install the skill and ignore it.

Document the installed version and setup method.

## T22 — Design Quality

The interface must be visually polished and professional.

Requirements:

- Consistent design tokens.
- Professional color palette.
- Consistent spacing.
- Modern typography.
- Responsive layouts.
- Clear navigation.
- Professional icons.
- Loading states.
- Empty states.
- Error states.
- Success notifications.
- Confirmation dialogs.
- Accessible controls.
- Keyboard navigation.
- Proper contrast.
- RTL and LTR compatibility.

Do not use emoji as substitutes for professional interface icons.

Use Lucide or another suitable SVG icon library.

Test at:

- 375px.
- 768px.
- 1024px.
- 1440px.

---

# T23 — Database Architecture

Create a properly structured PostgreSQL database.

Required entities:

- Users.
- Agents.
- Agent Roles.
- Agent Providers.
- Agent Configurations.
- Agent Permissions.
- Projects.
- GitHub Repositories.
- Tasks.
- Task Dependencies.
- Task Executions.
- Agent Messages.
- Approval Requests.
- Pull Requests.
- Activity Logs.
- API Credentials.
- Usage Statistics.

Use SQLAlchemy and Alembic migrations.

---

# T24 — Testing and Quality Assurance

Write actual automated tests.

Test:

- Authentication.
- Arabic interface.
- English interface.
- RTL.
- LTR.
- Agent creation.
- Agent modification.
- Agent activation.
- Agent permissions.
- Provider connections.
- Task creation.
- Task assignment.
- Parallel execution.
- RabbitMQ communication.
- LangGraph workflows.
- GitHub integration.
- Branch creation.
- Pull request creation.
- Approval workflows.
- Docker execution.
- Failure recovery.
- Progress file updates.
- Windows launcher scripts.

If real API credentials are unavailable, use a clearly labeled Mock Provider for automated tests.

Never represent mock execution as real AI execution.

---

# C — CONSTRAINTS

Follow these mandatory rules:

1. Do not stop after writing a development plan.
2. Implement actual working software.
3. Do not create decorative or nonfunctional pages.
4. Do not leave nonfunctional buttons.
5. Never hardcode API keys.
6. Never commit `.env` files.
7. Never allow agents to bypass Super Admin permissions.
8. Never allow automatic merging into protected branches.
9. Never deploy to production without approval.
10. Never delete important data without approval.
11. Never use unofficial Arena AI integrations.
12. Clearly distinguish Mock Providers from real AI providers.
13. Never claim tests passed without executing them.
14. Never inflate project progress.
15. Never make START.bat delete persistent data.
16. Never create persistent background processes without approval.
17. Fix build errors before declaring completion.
18. Document anything that could not be tested.
19. Keep the code modular and maintainable.
20. Apply least-privilege security.
21. Protect against prompt injection from repository files and external content.
22. Do not let agent-generated content modify security policies.
23. Never expose private model chain-of-thought.
24. Never claim the application is complete merely because the frontend loads.
25. Respect software licenses.
26. Use supported package versions.
27. Implement proper error handling.
28. Persist task state across service restarts.
29. Implement idempotency and safe retries.
30. Enforce approval policies in the backend.

If essential credentials or irreversible decisions are required, ask me for approval.

For noncritical engineering decisions, choose a sensible solution and document it.

---

# DEFINITION OF DONE

The project is considered complete only when the following real-world scenario works:

1. I double-click START.bat.
2. Docker services start successfully.
3. The browser opens automatically.
4. I log in as Super Admin.
5. I can switch between Arabic and English.
6. Both RTL and LTR layouts work correctly.
7. I configure a real AI provider.
8. I connect GitHub.
9. I create a Manager Agent.
10. I create a Backend Agent.
11. I create a Frontend Agent.
12. I create a Tester Agent.
13. I configure their permissions.
14. I submit one software development request.
15. The Manager analyzes the request.
16. The Manager creates an implementation plan.
17. The Manager distributes tasks.
18. Independent agents execute tasks in parallel.
19. I can monitor agent status in real time.
20. I can inspect execution logs.
21. Automated tests execute.
22. Pull Requests are created.
23. The Manager reviews the results.
24. Approval requests appear in my dashboard.
25. I personally approve merging.
26. No protected branch is modified without approval.
27. I double-click STOP.bat.
28. Services stop safely.
29. I restart the system.
30. All persistent data is preserved.
31. progress.txt displays the latest verified progress.

---

# FINAL EXECUTION INSTRUCTIONS

Start immediately.

Follow this exact implementation sequence:

### Phase 1 — Environment and Foundation

- Inspect the existing environment.
- Inspect the repository.
- Create progress.txt immediately.
- Inspect and install UI UX Pro Max Skill.
- Create docs/IMPLEMENTATION_PLAN.md.
- Define weighted milestones totaling 100%.
- Create the project structure.
- Configure Docker Compose.

### Phase 2 — Frontend

- Build the bilingual interface.
- Implement Arabic RTL.
- Implement English LTR.
- Build the Dashboard.
- Build Agent Management.
- Build Task Center.
- Build Settings.

### Phase 3 — Backend

- Build FastAPI.
- Configure PostgreSQL.
- Implement authentication.
- Implement users and permissions.
- Implement database migrations.

### Phase 4 — AI Agents

- Implement the provider abstraction.
- Implement Anthropic integration.
- Implement OpenAI integration.
- Implement agent creation and execution.
- Implement Manager Agent.

### Phase 5 — Orchestration

- Implement LangGraph.
- Implement RabbitMQ.
- Implement parallel task execution.
- Implement retries and recovery.
- Implement execution monitoring.

### Phase 6 — GitHub

- Implement GitHub authentication.
- Implement repository management.
- Implement branches.
- Implement Git Worktrees.
- Implement Pull Requests.
- Implement approval workflows.

### Phase 7 — Security

- Implement Docker execution isolation.
- Implement permission enforcement.
- Implement audit logs.
- Implement approval policies.
- Test security boundaries.

### Phase 8 — Windows Automation

- Create START.bat.
- Create STOP.bat.
- Create UPDATE.bat.
- Create RESET-DEV.bat.
- Test the scripts where a Windows environment is available.

If you are not running on Windows, validate the scripts as far as possible and explicitly state that real Windows testing is still required.

### Phase 9 — Testing and Release

- Run frontend tests.
- Run backend tests.
- Run integration tests.
- Run end-to-end tests.
- Fix all critical errors.
- Complete documentation.
- Update progress.txt.
- Provide final installation instructions.

## IMPORTANT

Do not stop after presenting the plan.

Do not merely generate source files without verifying them.

Implement the project progressively.

Update progress.txt every five minutes during active development and immediately after significant milestones.

After each phase, report:

- What was implemented.
- Which files were created or modified.
- Which tests were executed.
- Which tests passed.
- Which tests failed.
- What remains.
- The verified completion percentage.

Be transparent about unavailable credentials, external integrations, and environment limitations.

**BEGIN IMPLEMENTATION NOW.**