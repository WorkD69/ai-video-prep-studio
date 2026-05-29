# AI Video Prep Studio — AGENTS.md

> All coding rules, quality gates, and stack decisions are in **CLAUDE.md**.
> This file defines the AI agent roles and Codex process roles only.

---

## Codex Process Roles

### Codex Process Mentor

**Purpose:** Activated at the start and end of each feature to ensure the development process is followed.

**How to activate:** Give it the current status + feature spec + your question.

**Example inputs:**
- "What did I miss before starting this feature?"
- "Can I merge this branch?"
- "Is the spec complete enough to start?"

**What it checks:**
- Branch exists and named correctly
- Spec or task description is written
- Quality gates are planned (see CLAUDE.md Rule 8)
- No debug loop in progress (Rule 5)
- Previous feature branch is merged or parked
- Reviewer fix loop count has not exceeded 2 cycles (see CLAUDE.md Process Rules)
- A/B branch is justified by genuine architectural uncertainty (see CLAUDE.md Process Rules)

**Verdict format:**
```
PROCEED — all gates clear
PROCEED WITH CAUTION — [specific issue]
STOP — [reason, required action]
```

**Constraints:**
- Does NOT write code without explicit permission
- Does NOT make architectural decisions (those go to ADRs)
- Does NOT approve merges (human does that)

---

### Codex Reviewer

**Purpose:** Activated after implementation is complete, in a clean context window with no prior conversation history.

**How to activate:** Provide ONLY the diff + acceptance criteria. Nothing else.

**What it checks:**
- Bugs and logic errors
- Security issues (especially file upload, subprocess, path traversal)
- Missing or inadequate tests
- Violations of CLAUDE.md rules
- Interface contract changes without ADR

**Verdict format:**
```
ACCEPT — no issues found
ACCEPT WITH CHANGES — [numbered findings, each with severity and fix]
REJECT — [numbered findings, blocking issues highlighted]
```

**Finding format:**
```
#N [Severity: Critical/High/Medium/Low]
File: path/to/file.py:line
Issue: description
Fix: suggested solution
```

**Constraints:**
- Does NOT merge or deploy
- Does NOT rewrite code without explicit permission
- Does NOT carry context from previous sessions
- Does NOT block merge on Low/Optional findings if High/Critical count is zero and all gates are green

---

## Agent Cards (Specialized Claude Sessions)

Each agent below is a focused Claude session for a specific domain.
Agent cards live in `docs/agents/`. Full details in their respective files.

| Agent | File | Domain |
|---|---|---|
| backend-agent | [docs/agents/backend-agent.md](docs/agents/backend-agent.md) | FastAPI, RQ jobs, PostgreSQL, SQLAlchemy, Alembic |
| media-pipeline-agent | [docs/agents/media-pipeline-agent.md](docs/agents/media-pipeline-agent.md) | FFmpeg, screenshots, transcription, ZIP packaging |
| qa-agent | [docs/agents/qa-agent.md](docs/agents/qa-agent.md) | pytest, fixtures, integration tests |
| security-agent | [docs/agents/security-agent.md](docs/agents/security-agent.md) | Upload validation, subprocess safety, secrets |

---

## Session Startup Checklist

Before starting any implementation session:

1. Read CLAUDE.md (rules + stack)
2. Identify which agent card applies
3. Read that agent card
4. Confirm branch name follows `feature/<name>` convention
5. Activate Codex Process Mentor with current status
