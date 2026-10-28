# TacoSort (was TidyInbox): who did what

Every session's transcript stays on disk at `~/.claude/projects/D--ClaudeProjects-Email-Organization/<id>*.jsonl`.
`WORKFLOW.html` is the drawn picture of these runs.

## 2026-10-28: Nodeterm team builds, reviews and publishes TidyInbox

### At a glance
15 Claude sessions: 1 orchestrator, 4 builders, 1 integrator, 3 reviewers, 5 fixers, 1 re-checker.

```
Orchestrator (spec, contracts, briefs)
  ├─ IMAP ─┐
  ├─ Classifier ─┤
  ├─ Engine ─┼─> Integrator ─> Review panel (correctness, security, tests)
  └─ CLI+Docs ─┘                     │
                                     v
          Fix-IMAP, Fix-Classifier, Fix-Engine, Fix-Learner, Fix-CLI ─> Re-check
                                     │
                                     v
          Orchestrator: N-1/N-2 fixes, rename, publish, CI fixes
```

### Timeline (local time, CDT)
| Time | What happened |
|---|---|
| 01:32 | User asks for a public, multi-account email organizer built with nodeterm, no worktrees |
| 01:32-01:43 | Orchestrator writes SPEC.md, shared models/backend/FakeBackend, briefs |
| 01:43-02:06 | Four builder stations work in parallel |
| 02:03-02:18 | Integrator wires the parts together, adds end-to-end tests |
| 02:16-02:32 | Three-lens review panel |
| 02:34-02:50 | Fix wave (Fix-IMAP and Fix-Engine sessions show activity until 03:42 / 03:47) |
| 02:53-03:05 | Re-check of the fixes |
| 03:05-03:20 | Orchestrator fixes N-1/N-2, user picks the name TidyInbox and "I create + push it" |
| 03:20-04:12 | Quiet gap in the orchestrator (cause not read) |
| 04:12 | Rename, public repo, CI fixes; CI green on all six jobs |
| 12:02 | User asks to save progress |

### Every session
| Stage | Session | Role | Time | Changed | Did / found | Outcome |
|---|---|---|---|---|---|---|
| Lead | 979da9f6 | Orchestrator (this session) | 01:32-12:02 | SPEC, shared contracts, briefs, pipeline/client/engine/store fixes, tests | Planned, briefed, merged, fixed re-check findings, renamed, published, fixed CI | done |
| Build | 5fd5ed29 | IMAP station | 01:43-02:02 | `imap/` + its tests | Providers, keychain + OAuth, ImapBackend | done |
| Build | d1d3fb18 | Classifier station | 01:43-02:03 | `classify/` + its tests | Guards, rules, heuristics, AI; 149 tests pass | done |
| Build | ea1b0945 | Engine station | 01:43-02:04 | engine, learner, schedule, store + tests | 68 tests pass | done |
| Build | 2193daad | CLI and Docs station | 01:43-02:06 | cli, config, README, docs, CI workflow, issue templates | CLI, docs, CI | done |
| Integrate | 67c07b7c | Integrator | 02:03-02:18 | cli, engine, report, heuristics, e2e tests | Found the schedule command line would always fail; new-mail-only plain run | done |
| Review | 22b89d05 | Correctness reviewer | 02:16-02:28 | `_team/reviews/correctness.md` | 2 blockers (Gmail labels, UIDVALIDITY sweep), 6 should-fix | done |
| Review | db659062 | Security reviewer | 02:16-02:26 | `_team/reviews/security.md` | No blockers, 4 should-fix, 8 smaller | done |
| Review | 333e313f | Tests reviewer | 02:16-02:32 | `_team/reviews/tests.md` | Fake server too perfect (no Gmail labels, no partial moves) | done |
| Fix | 9135fb92 | Fix-IMAP | 02:34-03:42 | imap auth/client + tests | 10 findings: safe move after drops, folder case, keychain names; 86 tests | done |
| Fix | e5860faf | Fix-Classifier | 02:34-02:46 | classify + tests | 5 findings incl. AI injection guard, verify-email; 202 tests | done |
| Fix | fae196c1 | Fix-Engine | 02:34-03:47 | engine, store (schema v2) + tests | 12 items; suite 563 passed | done |
| Fix | 865aaad4 | Fix-Learner | 02:34-02:50 | learner, schedule + tests | Gmail-label learning, per-user scheduled task; 50 tests | done |
| Fix | d3fe880e | Fix-CLI | 02:34-02:49 | cli, config, docs | 10 items: install from GitHub, reserved ids, umask, keychain cleanup | done |
| Check | d1b48d8d | Re-check | 02:53-03:05 | `_team/reviews/recheck.md` | All blockers fixed; new N-1 (learned rule vs security codes), N-2 (MOVE + search lag) | done |

### What went wrong, and the lesson
- `spawn-team` and `verify` were refused because the project was off screen: stations were opened one
  by one with `open-claude --run-now`; armed stations stayed QUEUED until `run --node`.
- A heredoc started an interactive Python that had to be stopped; spawn scripts were written as files
  instead.
- Fix stations reported full-suite failures that came from other stations editing at the same time in
  the shared tree; the orchestrator's own full run after the wave was the real check.
- The fake mail server was "perfect" (no Gmail labels, no partial moves, no search lag), so the
  builders' tests could not see real-server bugs: the review added those modes to FakeBackend.
- The name was picked before checking PyPI; "mailtidy" was taken, so the project was renamed late.
- Short random run ids (4 hex) collided on fast CI machines and overwrote a run record.
- No prompt was cut off (no `LOOKS CUT OFF` flags).
