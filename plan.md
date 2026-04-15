# Hypothesis Catalog — Copilot Adoption Drivers & Deterrents

What makes developers adopt GitHub Copilot? What makes them stop? This document lists every testable hypothesis, what data we need, and whether we can test it today.

**Status key:**
- **Tested** — already implemented in our analysis code
- **Ready** — data available in DX or ServiceNow, not yet in the pipeline
- **Needs data** — requires a data source we don't currently have

---

## 1. Individual Developer Factors

These hypotheses ask: does something about the **person** predict whether they adopt?

| # | Hypothesis | Test | Data needed | Source | Status |
|---|-----------|------|-------------|--------|--------|
| 1.1 | Senior engineers adopt more than juniors | Compare adoption rate by job title/level | `job_title` | sys_user | **Tested** — `analyze_drivers.py` group tests |
| 1.2 | Longer-tenured employees adopt slower (comfort with existing workflow) | Correlate `tenure_at_first_use_days` with adoption | `hire_date` | sys_user | **Tested** — logistic regression predictor |
| 1.3 | Some departments adopt more than others | Compare adoption rate and churn by department | `department` | sys_user | **Tested** — group tests + churn by group |
| 1.4 | Location/timezone affects adoption (e.g., remote workers adopt more) | Compare adoption rate by office/location | `location` | sys_user | **Tested** — group tests |
| 1.5 | Developers who attended Copilot training adopt faster and stick longer | Compare time-to-first-use and churn rate: trained vs untrained | Training attendance records (date, user, completion) | LMS / training platform export | **Needs data** |
| 1.6 | Developers with prior AI tool experience (e.g., Cursor, Tabnine) adopt faster | Compare adoption speed for users who previously used other AI tools | Prior tool usage history | IT asset management or survey | **Needs data** |
| 1.7 | Developers who code more days per week adopt more (higher exposure) | Correlate pre-Copilot commit frequency with adoption | `github_pull_commits` commit frequency per user before Copilot | DX: github_pull_commits | **Ready** — data loaded but not analyzed |

---

## 2. Team and Manager Factors

These hypotheses ask: does the **team environment** influence adoption?

| # | Hypothesis | Test | Data needed | Source | Status |
|---|-----------|------|-------------|--------|--------|
| 2.1 | If your manager uses Copilot, you're more likely to adopt | Compare adoption rate where manager is adopter vs non-adopter | `manager` field from sys_user + Copilot usage for that manager | sys_user (manager), copilot_daily_usages | **Ready** — both tables loaded, need cross-reference logic |
| 2.2 | Teams with higher peer adoption have higher individual adoption (peer influence) | Correlate individual adoption with team adoption rate | `github_teams`, `github_team_memberships`, copilot daily | DX: teams + memberships | **Ready** — data loaded, need team-level aggregation |
| 2.3 | TAOs (Technical Asset Owners) who actively commit code drive team adoption | Compare team adoption where TAO commits regularly vs rarely | TAO designation per repo + commit frequency | ServiceNow CMDB (app-to-TAO mapping) + github_pull_commits | **Needs data** — TAO mapping not in current exports |
| 2.4 | TAOs who attended Copilot training drive higher team adoption | Compare team adoption where TAO completed training | TAO designation + training attendance | CMDB + LMS | **Needs data** |
| 2.5 | Larger teams adopt slower (coordination overhead, slower consensus) | Correlate team size with team-level adoption rate | team_memberships count per team | DX: github_team_memberships | **Ready** — data loaded |
| 2.6 | Teams where a reviewer uses Copilot influence the PR author to adopt (review exposure) | Compare adoption for authors whose PRs were reviewed by Copilot users | `github_reviews` (reviewer user_id) cross-referenced with Copilot adopter list | DX: github_reviews + copilot_daily_usages | **Ready** — both loaded, need cross-reference |

---

## 3. Repository and Code Factors

These hypotheses ask: does the **nature of the code** affect whether Copilot is useful?

| # | Hypothesis | Test | Data needed | Source | Status |
|---|-----------|------|-------------|--------|--------|
| 3.1 | Developers working on newer repos adopt more (less legacy friction) | Compare adoption for users who contribute to repos created recently vs old repos | Repo creation date, user-to-repo mapping | DX: github_repositories + github_pulls (repo_id per user) | **Ready** — repos loaded but creation date not analyzed |
| 3.2 | Some programming languages see higher adoption (Copilot is better for Python than C++) | Compare acceptance rate and adoption by primary language | Usage by language | DX: github_copilot_user_metric_by_language_features | **Ready** — table documented in schema, not yet loaded |
| 3.3 | Repos with AI guidance files (.github/copilot-instructions.md, CLAUDE.md, .cursorrules) have higher adoption | Compare adoption for users contributing to repos with vs without guidance files | Repo file listing or metadata scan | GitHub API repo contents scan or manual audit | **Needs data** — not in DX exports |
| 3.4 | More complex repos (high LOC, many contributors) see lower adoption (harder for AI to help) | Correlate repo complexity with user adoption rate for contributors | Repo LOC, contributor count, commit frequency | DX: github_repositories + github_pull_commits | **Ready** — data loaded, need repo-level metrics |
| 3.5 | Repos with high test coverage see higher AI code retention (safer to accept suggestions) | Correlate test coverage with AI code retention rate | Test coverage metrics per repo + ai_code_commits | CI/CD pipeline metrics (e.g., SonarQube) | **Needs data** |
| 3.6 | Frontend/UI repos see different adoption than backend/API repos | Compare adoption by app type classification | App type classification per repo | ServiceNow CMDB (application classification) | **Needs data** — requires app-to-repo mapping |

---

## 4. Tooling and Infrastructure Factors

These hypotheses ask: does the **tooling setup** help or hinder adoption?

| # | Hypothesis | Test | Data needed | Source | Status |
|---|-----------|------|-------------|--------|--------|
| 4.1 | VS Code users adopt more than JetBrains users (better plugin experience) | Compare adoption and acceptance rate by IDE | Usage by IDE | DX: github_copilot_user_metric_by_ides | **Ready** — table documented, not yet loaded |
| 4.2 | Users who try chat/agent features stick longer than completion-only users | Compare churn rate: used_chat=true vs false | Feature usage flags | DX: github_copilot_user_metric_by_features | **Ready** — daily table has flags, breakdown table adds detail |
| 4.3 | Users on newer AI models have higher acceptance rates (better suggestions) | Compare acceptance rate by model | Usage by model | DX: github_copilot_user_metric_by_language_models | **Ready** — table documented, not yet loaded |
| 4.4 | Users who hit premium request rate limits churn more (frustration) | Compare churn rate for users with high vs low premium request counts | Premium request counts per user per day | DX: github_copilot_premium_request_usages | **Ready** — table documented, not yet loaded |
| 4.5 | Frequency of hitting rate limits correlates with lower adoption | Correlate days-with-max-requests with adoption/churn | Premium request daily patterns | DX: github_copilot_premium_request_usages | **Ready** — same table |
| 4.6 | Users with outdated Copilot plugins have worse experience and lower retention | Compare acceptance rate and churn by plugin version | Plugin version per IDE | DX: github_copilot_user_metric_by_ides (extensions_path) | **Ready** — table documented, not yet loaded |
| 4.7 | CLI usage (terminal Copilot) is a power-user signal | Check if CLI users have higher overall adoption and retention | CLI usage per user | DX: github_copilot_user_metric_by_clis | **Ready** — table documented, not yet loaded |

---

## 5. Organizational and Process Factors

These hypotheses ask: does the **rollout and governance structure** affect adoption?

| # | Hypothesis | Test | Data needed | Source | Status |
|---|-----------|------|-------------|--------|--------|
| 5.1 | Longer time since license assignment = lower chance of ever adopting | Correlate days-since-seat-assigned with first-use occurrence | Seat assignment date per user | GitHub Copilot admin / license management export | **Needs data** — not in DX |
| 5.2 | Teams with a guided rollout (kickoff meeting, champion) adopt more than self-serve | Compare adoption for guided vs self-serve teams | Rollout method per team | Internal rollout tracking (manual or project management tool) | **Needs data** |
| 5.3 | Repos under security/compliance restrictions see lower adoption (fear of IP exposure) | Compare adoption for users working on restricted vs unrestricted repos | Repo classification (restricted/public/internal) | GitHub repo visibility + org security policies | **Needs data** |
| 5.4 | Departments aware of per-seat cost show different adoption (either higher accountability or cost aversion) | Compare adoption for cost-center-aware vs unaware departments | Cost center + awareness survey | Finance/procurement + survey | **Needs data** |
| 5.5 | Top-down mandated teams adopt faster initially but churn more (forced vs organic) | Compare time-to-first-use AND churn for mandated vs organic teams | Mandate tracking per team/department | Internal rollout records | **Needs data** |

---

## 6. Productivity and Quality Outcomes

These hypotheses ask: does Copilot actually **make things better**, and does that affect stickiness?

| # | Hypothesis | Test | Data needed | Source | Status |
|---|-----------|------|-------------|--------|--------|
| 6.1 | PR cycle time decreases after Copilot adoption | Compare median open_to_merge before vs after first_use | github_pulls + copilot_daily_usages (for first_use date) | DX | **Tested** — `analyze_productivity.py` before/after |
| 6.2 | Developers keep most AI-generated code (high retention = useful suggestions) | Measure mean ai_code_retained per user | ai_code_commits | DX | **Tested** — AI retention analysis |
| 6.3 | AI code retention rate is improving over time (models getting better) | Track weekly ai_code_retained trend | ai_code_commits with commit_timestamp | DX | **Tested** — retention over time |
| 6.4 | Users who see productivity improvement stick longer (positive feedback loop) | Correlate personal cycle-time improvement with retention/churn | github_pulls before/after + churn flag | DX | **Ready** — data available, need to cross-reference productivity delta with churn |
| 6.5 | Code review turnaround improves for Copilot users (reviewers can review AI-assisted PRs faster) | Compare time_since_request for PRs by adopters vs non-adopters | github_reviews + github_review_requests | DX | **Ready** — tables loaded but not analyzed |

---

## Summary: What Can We Do Today?

| Status | Count | Action |
|--------|-------|--------|
| **Tested** | 9 hypotheses | Already in the pipeline — run the analysis |
| **Ready** | 14 hypotheses | Data exists in DX or can be derived from loaded tables — need to add analysis code |
| **Needs data** | 10 hypotheses | Requires exports from LMS, CMDB, license management, or manual audit |

### Highest-value "Ready" hypotheses to add next

These use DX tables we've already documented but haven't loaded:

1. **4.1 IDE adoption** — `github_copilot_user_metric_by_ides` (are JetBrains users underserved?)
2. **3.2 Language adoption** — `github_copilot_user_metric_by_language_features` (is Copilot weaker for certain languages?)
3. **4.4 Rate limits as deterrent** — `github_copilot_premium_request_usages` (are people hitting walls?)
4. **2.1 Manager influence** — cross-reference sys_user.manager with Copilot user list (no new table needed)
5. **2.6 Review exposure** — cross-reference github_reviews with adoption dates (no new table needed)

### Highest-value "Needs data" hypotheses to pursue

These require new data sources but could have high explanatory power:

1. **1.5 Training attendance** — ask L&D team for LMS export
2. **2.3 TAO engagement** — ask CMDB team for app-to-TAO mapping
3. **3.3 AI guidance files** — scan repos for .github/copilot-instructions.md presence
4. **5.1 License assignment timing** — ask Copilot admin for seat assignment dates
