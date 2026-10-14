# Example: a scientific research pipeline

How Duo can support each stage of a research project, from observations to a
paper with a confirmed reference set. Duo's role is the same at every stage: two
reviewers write or check **one document** against the files in your project
until both approve it. Duo does not run simulations or experiments, and it
writes nothing but that document.

Everything here uses experimental features: document modes (`--mode`,
`--instructions-file`, `--output`) and web access (`--allow-web`). What has been
tested live is listed at the end. Read [docs/SECURITY.md](../docs/SECURITY.md)
before using `--allow-web`.

## Set up once

Keep the project in one Git repository, so every document Duo writes is
versioned, and commit after each stage:

```sh
mkdir -p ~/research/phenomenon && cd ~/research/phenomenon && git init
mkdir -p data literature docs sims analysis paper
export COPILOT_GITHUB_TOKEN="$(cat ~/.copilot-token)"

P=~/research/phenomenon
pair=(--codex-backend copilot --codex-model gpt-6-astra --codex-effort high
      --claude-backend copilot --claude-model claude-opus-5.5 --claude-effort high)
duo2()    { duo --repo "$P" "${pair[@]}" "$@"; }                         # offline, whole project
duo2web() { duo --repo "$P/literature" --allow-web "${pair[@]}" "$@"; }  # online, literature/ only
```

- **Models:** the pair above runs both reviewers through GitHub Copilot. For
  native CLIs, drop the backend options and use native model names, for example
  `--codex-model gpt-6-astra --claude-model claude-opus-5-5`.
- **Web runs see only `literature/`.** With web access, text a reviewer reads
  can steer it into sending data out through queries and URLs. Copilot cannot
  read outside `--repo`, including the rest of the same Git repository (tested:
  a reviewer started in `literature/` was refused `../docs/`). Your data,
  hypotheses, and drafts stay out of reach of web-enabled runs. Native Codex can
  read anywhere, so do not use it with `--allow-web` for unpublished work.
- **Copilot fetches but does not search.** It reaches the literature through
  APIs it can fetch: Crossref, OpenAlex, arXiv.
- **Text only:** reviewers read text files. Convert papers with `pdftotext` and
  spreadsheets to CSV.
- **Checklists:** keep one per stage under `~/duo-tasks/` and pass it with
  `--instructions-file`; two are given below.

## The pipeline

| # | Stage | Duo's role | Mode | Web | Output |
| --- | --- | --- | --- | --- | --- |
| 1 | Observations | Summarize and quantify raw data | `report` | – | `docs/observations.md` |
| 2 | Literature search | Find prior work with DOIs | `report` | ✓ | `literature/search.md` |
| 3 | Literature review | Check your summary against the papers | `review` | – | `docs/literature-review.md` |
| 4 | Hypotheses | Draft them, then review them | plan with a checklist, then `review` | – | `docs/hypotheses.md` |
| 5 | Validation design | Convergence tests, benchmarks, pilot runs | plan | – | in `docs/simulation-plan.md` |
| 6 | Simulation or experiment plan, with an analysis plan | Draft and review | plan | – | `docs/simulation-plan.md` |
| 7 | Performing the work | Check inputs; take stock of runs | `review`, `description` | – | `docs/inputs-review.md`, `docs/run-status.md` |
| 8 | Analysis | Results with every number checked against your outputs | `report` | – | `docs/results.md` |
| 9 | Interpretation and iteration | Decide which hypotheses held; revise | `review`, then back to 4 | – | updated `docs/hypotheses.md` |
| 10 | Paper | Review each section against the evidence | `review` | – | `paper/review-*.md` |
| 11 | References | Confirm entries online; check claims against sources | `report`, `review` | ✓, – | `literature/refs-check.md`, `paper/citations-review.md` |
| 12 | Submission and referees | Check the response against reports and revisions | `review` | – | `paper/review-response.md` |

## 1. Observations

Put the measurements in `data/` as text or CSV:

```sh
duo2 --mode report --output docs/observations.md \
  --task "Summarize the observations in data/: what was measured, conditions, values with uncertainties, anomalies" \
  --criteria "Every number cites its file and rows; state what the data cannot show"
```

## 2. Literature search (online)

Duo cannot download files: fetch the papers it lists yourself, convert them with
`pdftotext`, and add them and their BibTeX entries to `literature/`.

```sh
duo2web --mode report --output search.md \
  --task "Find prior work on <phenomenon, system, conditions>: experiments, simulations, proposed mechanisms" \
  --criteria "Use api.openalex.org, api.crossref.org, and export.arxiv.org; list each work with authors, year, title, DOI, and one line on its relevance; mark anything not confirmed by a DOI record"
```

## 3. Literature review

Write `docs/literature-summary.md` from the papers, then:

```sh
duo2 --mode review --output docs/literature-review.md \
  --task "Review docs/literature-summary.md against the papers in literature/: is each statement supported by the cited text, and what relevant work in the folder is missing?"
```

## 4. Hypotheses

Write the drafting checklist once:

```sh
mkdir -p ~/duo-tasks
cat > ~/duo-tasks/hypothesis-drafting.txt <<'TXT'
Draft and review a hypotheses document for the phenomenon. Check that it:

- Restates the observations to be explained, citing their files.
- States each hypothesis (H0 as the null, then H1, H2, ...) with its mechanism,
  assumptions, and a falsifiable prediction: which observable changes, in which
  direction, and roughly by how much; and what result would refute it.
- Covers competing mechanisms, says which hypotheses are mutually exclusive and
  which could act together, and does not omit a plausible explanation.
- Includes a table of discriminating predictions: observables against
  hypotheses.
- Proposes an initial simulation or experimental approach for each hypothesis,
  noting where it cannot decide the question.
- Separates what the project files state from general knowledge. Does not invent
  citations or numbers; marks anything needing a source as "to verify".
- Ends with open questions for the researcher.
TXT
```

Draft:

```sh
duo2 --output docs/hypotheses.md --instructions-file ~/duo-tasks/hypothesis-drafting.txt \
  --task "Write docs/hypotheses.md: competing, testable hypotheses for the phenomenon in docs/observations.md, informed by docs/literature-summary.md"
```

If Duo exits with code 3, a reviewer needs a decision from you. Its question is
printed. Add your answer with `--criteria` and rerun; the draft in
`docs/hypotheses.md` is the starting point.

After editing the hypotheses yourself, review them with a second checklist:

```sh
cat > ~/duo-tasks/hypothesis-checks.txt <<'TXT'
Review the hypotheses for completeness and testability, and the proposed
approach for whether it can test them.

- Is each hypothesis precise, with a mechanism and a measurable, falsifiable
  prediction? Are competing explanations and a null hypothesis covered?
- Are assumptions, parameter ranges, and units stated and consistent?
- For each hypothesis: is the method suited to the length and time scales? Are
  the model, system size, conditions, and run length or sample size justified?
  Which observable decides it? Are controls, convergence checks, uncertainty,
  and validation planned? Which runs distinguish competing hypotheses?

Cite the line for every finding, rate it major or minor, and say what is already
sound. Do not rewrite the hypotheses.
TXT
duo2 --mode review --output docs/hypotheses-review.md \
  --instructions-file ~/duo-tasks/hypothesis-checks.txt \
  --task "Review whether docs/hypotheses.md is complete and testable" \
  --criteria "End with a prioritized list of missing hypotheses and missing simulations or analyses"
```

## 5–6. Validation and main plan, with the analysis plan

Plan validation before production, and fix the analysis before any results
exist:

```sh
duo2 --output docs/simulation-plan.md \
  --task "Plan the validation runs and the production simulations testing each hypothesis in docs/hypotheses.md, addressing docs/hypotheses-review.md" \
  --criteria "Validation first: convergence, finite-size, and benchmark runs with pass criteria. Then per hypothesis: method, system, parameters, observables, controls, cost. Include a pre-specified analysis plan: statistics, uncertainty, and the result that supports or refutes each hypothesis"
git add -A && git commit -m "Plan v1" && git tag plan-v1
```

The tag shows that the analysis plan was fixed before the results were seen.

## 7. Performing the work

Duo does not run anything. Around the work:

- **Scripts and inputs:** plan mode, Duo's default, plans code changes. Use it in
  the code repository, then implement the plan yourself or with a coding agent.
- **Before production**, check the inputs against the plan:

  ```sh
  duo2 --mode review --output docs/inputs-review.md \
    --task "Check the inputs in sims/ against docs/simulation-plan.md: parameters, units, ensembles, run lengths"
  ```

- **During and after**, take stock of the runs:

  ```sh
  duo2 --mode description --output docs/run-status.md \
    --task "Describe the state of every run in sims/: completed, aborted, or missing, with evidence"
  ```

## 8. Analysis

Compute with your own scripts, writing results to `analysis/` as text or CSV.
Through Copilot, reviewers cannot run code; they check numbers by reading, so
give them summary tables rather than raw trajectories.

```sh
duo2 --mode report --output docs/results.md \
  --task "Report the results in analysis/ for each hypothesis in docs/hypotheses.md, following the analysis plan in docs/simulation-plan.md (tag plan-v1)" \
  --criteria "Every number cites its file and rows; list deviations from the pre-specified plan; say which hypotheses are supported, refuted, or undecided"
```

## 9. Interpretation and iteration

Review `docs/results.md` against the hypotheses. Undecided or refuted hypotheses
send you back to stage 4. Rerunning a stage starts from its existing document.

## 10. Paper

Write section by section; each run sends the whole document on every turn.

```sh
duo2 --mode review --output paper/review-results.md \
  --task "Review paper/results.tex against docs/results.md and analysis/: every claim and number supported, nothing overstated"
```

## 11. References

**(a) Online:** confirm every entry against its DOI record:

```sh
duo2web --mode report --output refs-check.md \
  --task "Verify each entry in refs.bib against its DOI record online: does the DOI resolve, and do authors, title, journal, volume, pages, and year match?" \
  --criteria "One row per entry with a verdict (confirmed, mismatch, or not found) and the URL checked; list every mismatching field"
```

**(b) Offline:** check that each citation supports its claim:

```sh
duo2 --mode review --output paper/citations-review.md \
  --task "Check every citation in paper/*.tex against literature/refs.bib and the texts in literature/: entry exists, and the cited text supports the claim" \
  --criteria "List uncited entries, citations without entries, and claims whose source text is not in literature/"
```

The set is complete and confirmed when (a) confirms every entry and (b) finds
every claim supported, with no gaps.

## 12. Submission and referees

```sh
duo2 --mode review --output paper/review-response.md \
  --task "Check paper/response.md against the referee reports in paper/referees/ and the revised paper: every point answered, every claimed change actually made"
```

## Steps often missed

1. **Literature search and novelty check** before the hypotheses; the reference
   set starts there.
2. **Validation and pilot runs:** show the method can resolve the effect before
   spending production compute.
3. **Pre-registration:** fix predictions and the analysis plan before results.
4. **Data quality control:** calibration, controls, and replicates for
   experiments; equilibration checks and failed-run rejection for simulations.
5. **Uncertainty and sensitivity analysis:** how conclusions depend on the
   model, parameters, and statistics.
6. **Iteration:** results usually send you back to the hypotheses.
7. **Reproducibility and archiving:** software versions, environment, input-to-
   output provenance, and deposited data and code for the data-availability
   statement.
8. **Co-author review, submission, and the response to referees.**
9. **Permissions and disclosure:** whatever reviewers read goes to the model
   providers (and, through Copilot, to GitHub); with `--allow-web`, queries go
   to search providers too. Check what your institution and collaborators allow,
   and the journal's policy on disclosing AI assistance.

## What has been tested

- **Live:** report, description, and review modes on synthetic simulation data
  (Linux and macOS), and `--allow-web` reference checking against DOI records in
  report mode (Linux, native and Copilot pairs), which is stage 11(a). Copilot's
  refusal to read outside a `--repo` subfolder (Linux).
- **Not tested:** literature search with `--allow-web`, and the pipeline end to
  end on a real project.
- **Always:** agreement between two models is a strong filter, not proof. Read
  each document, and check web-derived claims against the URLs it gives.
