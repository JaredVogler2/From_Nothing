# Research library — FOCUS / NEO

This directory is the **research** subfolder requested for the FROM_NOTHING project. The authoritative list of 15 ranked candidate publications is `papers.json`. These are candidate methods, not approved production features.

**Paper access:** Publisher-access papers may be paywalled. Never commit unauthorized copies. `fetch_open_papers.py` downloads only entries with explicitly listed public PDF URLs, verifies PDF signatures, and records failures. Open-web entries require manual license and PDF-link verification. Existing research PDFs remain at repository root; the integration agent must detect and cross-reference them rather than silently duplicating them.

**Entry point:** `INTEGRATION_MASTER_PROMPT.md`.

**Source architecture:** `../FOCUS_FROM_NOTHING_MASTER.txt` and `../FROM-NOTHING-APP/`. The master specification and all independent safety/acceptance gates remain authoritative.

**Ranking criteria:** aerospace-domain similarity, compatibility with named mechanics/QA/shift calendars/typed precedence, expected economic value, testability, deployment complexity, and published evidence quality. Ranks are an engineering prioritization, not empirical proof of superiority.

**Data handling:** synthetic data only in this public repo; no internal SQL output, credentials, employee identifiers, contract terms or company-controlled documents.
