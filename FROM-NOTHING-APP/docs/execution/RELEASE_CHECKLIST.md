# Release checklist: rollout and cutover (P24)

**Status: no step below the local section has been performed.** This build verified the application locally on
fixtures (`docs/execution/FINAL_ACCEPTANCE.md`). It installed no SQL, applied no grant, provisioned no key, activated
no CI and deployed nothing: the session had no authority over any concrete environment. Each step names the gate it
resolves (gates are defined once in `focus/release/gates.py` and listed in every review verdict).

Hosting is **Posit Connect first, then VMware Tanzu**, both on **CPython 3.12**. The REST API (`/api/v1`, OpenAPI at
`/api/v1/openapi.json`) serves people and other applications. MCP (`/mcp`, read-only, same identity and grants) stays
off until approved (OPT-03).

## 0. Local release candidate (done in this build, repeatable)

Run these from a fresh clone, in a new Python 3.12 virtual environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-deps -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m focus.cli acceptance --out evidence/24
.venv/bin/python -m focus.cli review --acceptance evidence/24/acceptance.json --reviewer "<name>"
```

- `acceptance` runs the whole suite once and judges A01-A72 and C01-C39 from per-test receipts. A failure or a
  skipped required test blocks the release.
- `review` writes the next unused `review/REVIEW-NNN.md` and `VERDICT-NNN.json`. It never overwrites, and it binds
  the manual, implementation, test, fixture-source, policy and acceptance hashes.
- Before any deployment, `python -m focus.cli review --verify NNN` on the commit being deployed must print
  `"matches": true`. Deploy only a reviewed implementation.

## 1. Independent review (LG-12)

- An independent reviewer examines the bound commit.
- They record their verdict with
  `python -m focus.cli review --kind INDEPENDENT_REVIEW --reviewer "<name>"`, which writes a new review number.
  The implementer self-checks (`REVIEW-001`...) are not independent.

## 2. Database (LG-01, LG-03; DBA with DDL rights)

1. Re-run `queries/discovery/fn_collisions.sql` against the target. The manual's 2026-10-27 observation of no
   `FOCUS_FN_%` objects is not proof for today.
2. `python -m focus.cli migrate --emit-dir install-bundle`, then review `install-bundle/MANIFEST.json` and record its
   `manifest_sha256`.
3. `python -m focus.cli preflight --role installer --probe-sql` in the installer's session.
4. `python -m focus.cli migrate --dry-run --catalog live`: expect only `PENDING` steps, with no `COLLISION`,
   `CHECKSUM_MISMATCH` or `DRIFT`.
5. `python -m focus.cli migrate --apply --catalog live --installer "<subject>" --confirm-manifest "<manifest_sha256>"`.
6. Apply `sql/grants/focus_fn_roles.sql`, then map one login per process: web, source, stager, signer, evaluator,
   observation producer and retention.

## 3. Live repository adapter (LG-02; engineering)

- Implement the repository protocol over the FOCUS_FN tables in `focus/data/sql_store.py`.
- Run the repository fault-injection suites against an installed non-production database.
- Until then a live host stays `REPOSITORY_UNAVAILABLE` by design.

## 4. Keys, identity and grants (LG-04, LG-10)

- **Signing key:** the security owner provisions the key (`FOCUS_SIGN_KEY_REF`, a reference, never inline) and its id (`FOCUS_SIGN_KEY_ID`) **only**
  in the signer's environment. Never give it to the web content.
- **Identity:**
  - Posit Connect: `FOCUS_IDENTITY_PROVIDER=posit_connect`.
  - Tanzu: `trusted_header`, with the SSO route service in front of the route.
- **Grants and scopes:** `FOCUS_GRANTS_JSON` per host. Name the excusal reviewers here (LG-09).
- **Application-to-application callers:**
  - On Posit Connect, the caller uses a Connect API key (`Authorization: Key ...`).
  - On Tanzu, the caller goes through the SSO proxy's service identity.
  - Either way, the caller's subject needs grants like any user.

## 5. Sources, producers and policy decisions (LG-06, LG-07, LG-08)

- **Source owners** populate `PERSON_TEAM_MAP` and `ATTENDANCE_SHIFT` (OD-10). They decide OD-01 to OD-14 as new
  policy versions, which never edit an existing policy hash.
- **Observation producers** (OD-15) are approved and granted `FOCUS_FN_ObservationProducer`.
- **The policy owner** approves the score policy (OD-16) by recording `score.policy_approval_ref`.

## 6. Posit Connect rollout (LG-11)

1. With the content's variables, run `python -m focus.cli preflight --role web`. It must exit 0.
2. `rsconnect deploy api --name <server> --entrypoint app:app --title "FOCUS Manufacturing Scheduling" .`
3. Set the Vars in `docs/execution/DEPLOYMENT.md`. Keep `FOCUS_MCP_ENABLED=false` until approved.
4. Check the deployed content:
   - `GET <content>/health/live` returns 200.
   - `GET <content>/health/ready` returns 200, or 503 with typed reasons while gates remain open.
   - `GET <content>/api/v1/context` shows the trusted identity and its grants.
   - Every HTML and API response carries `Cache-Control: no-store`.
5. Schedule the pipeline roles from a scheduler that holds each role's own credentials: the CI draft (LG-13), Tanzu
   tasks, or Connect scheduled content where the server allows it.

## 7. Non-production rehearsal of the live protocol (LG-05)

Run each role with its own credentials, in order:

```bash
python -m focus.cli pipeline --key <key> --role source
python -m focus.cli pipeline --key <key> --role app
python -m focus.cli pipeline --key <key> --role stager
python -m focus.cli pipeline --key <key> --role signer
python -m focus.cli pipeline --key <key> --role evaluator
python -m focus.cli pipeline --key <key> --status
```

The stager's unsigned stage and independent readback must complete before the signer runs; a signer started early
returns `blocked` and exits 1. Keep every receipt. This rehearsal is the first actual pre-sign SQL readback.

## 8. Cutover

1. The first executable acceptance is the signer's manual step, in a protected environment. The source head moves
   only forward, and the plan head moves last.
2. Team leads use the current plan. Every action is re-checked against current facts at dispatch (A53), so a revoked
   cert, a reopened shortage, an incomplete predecessor or withdrawn attendance blocks the action and requests repair.
3. Monitor as in `docs/execution/INSTALL_AND_PIPELINE.md` section 6:
   - missing or stale datasets;
   - lease takeovers and exhausted steps;
   - readback or signature failures;
   - commitment churn;
   - evaluations pending correction.

## 9. Tanzu rollout (after Connect)

1. Create the user-provided services `focus-sql` and `focus-secrets`, one per role app, holding that role's
   credentials only.
2. `cf push -f manifest.yml`. This starts the web process and the app-role worker
   (`worker --loop --role app`).
3. Run `python -m focus.cli preflight --role web` as a task. Use `--role worker` and `--role signer` for those apps.
4. Run the pipeline roles as tasks of their own apps, for example:
   `cf run-task focus-source --command "python -m focus.cli pipeline --key <key> --role source"`.
5. Health check: `http` on `/health/live`.

## 10. Rollback

See `docs/execution/ROLLBACK.md`. Accepted plans, original goals, source runs, events, commitments and evaluations
are preserved in every case.

## Remaining external actions

| Gate | Action | Owner |
| --- | --- | --- |
| LG-01 | Collision probe, then confirmed installer session for 001-009 | DBA |
| LG-02 | Live repository adapter over the FOCUS_FN tables, verified on non-production | Engineering |
| LG-03 | Apply grants and map one login per process | DBA |
| LG-04 | Provision the signing key in the signer environment only | Security owner |
| LG-05 | Rehearse stage, readback, sign and accept on non-production SQL | Engineering and DBA |
| LG-06 | Populate workforce feeds and decide OD-01 to OD-14 | Source owners |
| LG-07 | Approve and install observation producers (OD-15) | Operations |
| LG-08 | Approve the score policy (OD-16) | Policy owner |
| LG-09 | Name excusal reviewers in the host grants (OD-17) | Operations |
| LG-10 | Configure the identity provider and grants on each host | Platform administrator |
| LG-11 | Deploy the reviewed commit to Posit Connect, then Tanzu | Platform owner |
| LG-12 | Independent review of the bound hashes | Independent reviewer |
| LG-13 | Activate the CI draft with protected environments per role | CI owner |
| OPT-01 | Optional: decide on the CP-SAT stage (`SOLVER_ENABLEMENT.md`) | Owner |
| OPT-02 | Optional: approve a BCAI provider (OD-18) | Owner |
| OPT-03 | Optional: enable MCP for application-to-application callers | Platform owner |
| OPT-04 | Optional: add an economic curve beyond LINEAR_DAILY | Engineering |
