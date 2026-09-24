# Backlog

None of these are urgent. Roughly ordered by usefulness.

1. **Confirm whether the web-scrape fallback is ever needed.**
   Shared meetings the batch API doesn't return are scraped from
   `notes.granola.ai/d/<id>` (title and notes only: no transcript, dates or
   attendees). Find a meeting reachable only by share link and check whether
   `/v1/get-documents-batch` returns it. If it does, remove the fallback. If it
   doesn't, give scraped meetings a stable timestamp so `--sync` stops
   rewriting them on every run.

2. **Token priority.** `get_token_from_local` prefers Granola's plaintext token
   files over our own persisted credentials and copies them over ours. A stale
   or revoked leftover file would replace a working saved token. Harmless while
   Granola encrypts its token files, but it matters if plaintext files reappear.

3. **Mypy cleanup.** 41 errors, mostly `meeting.transcript` used without
   checking for `None`. Fix them, then run mypy alongside the tests so they
   don't return.

4. **CI.** A GitHub Actions workflow running pytest, ruff and mypy.

5. **CLI end-to-end tests.** `cli.py` has the thinnest coverage; `stats`,
   `show` and `search` have no end-to-end tests.

6. **`pyproject.toml` metadata.** Replace the placeholder `your-username` URLs
   and the "Contributors" author.

7. **HTML exporter template.** Move the 565-line f-string into a template file.
   Cosmetic.

8. **Optional: snapshot install for cron.** The cron job runs an editable
   install (`uv tool install -e`), so the checked-out code is what it runs.
   `uv tool install --force .` without `-e` would pin it to a snapshot, at the
   cost of reinstalling after each change.
