# Job Hunter

Personal automated job-hunting system. Searches job boards daily, scores
vacancies against your CV, removes duplicates, tracks everything in a CRM
and emails you the best matches.

**It never applies for anything on your behalf.** You approve and submit
every application yourself.

## Build status

| Phase | What it does | State |
|---|---|---|
| 1 | Connection audit | Complete |
| 2 | Architecture & config | Complete, tested |
| 3 | Database & CRM state | Complete, tested |
| 4 | Collectors (Reed, Adzuna) | Complete — Adzuna verified live |
| 5 | Filtering & deduplication | Complete, tested |
| 6 | Scoring engine | Complete, tested |
| 7 | Google Sheets CRM | Built — **never tested live** |
| 8 | Gmail alerts | Built — **never tested live** |
| 9 | Scheduler (GitHub Actions) | Fixed Oct 2026: once-per-day gate (old 09:00-only gate blocked every late run) |
| 10 | Dashboard & analytics | Complete |
| 11 | Full test suite | 134 tests passing |
| 12 | Deployment & user guide | Complete — see USER_GUIDE.md |

## Try it now

    cd job_hunter
    pip install -r requirements.txt
    python main.py --check-config
    python main.py --init-db
    python main.py --status

Run the tests:

    python -m unittest discover -s tests -t . -v

## Verification status

Everything above marked "tested" was tested offline with mock data.

**Nothing network-dependent has been verified.** The build environment had
no outbound internet, so no live API call, email send or spreadsheet write
has ever been executed. Those are proven on your machine with
`--test-sources` once your keys are in place.

## LinkedIn

Tested directly, not assumed: LinkedIn has no public jobs API, and
fetching an individual listing is blocked outright by their robots.txt
(`ROBOTS_DISALLOWED`). Their own search pages return only a count —
"3,000+ jobs — sign in to see them." That is a hard wall, not a
missing integration, and scraping past it would break Section 1's own
rule against bypassing bot detection.

**The route that stays inside the rules:** LinkedIn lets you create job
alert emails for a saved search. Once Phase 8 (Gmail) is built, a
collector can read those emails from your own inbox — that's an
authorised connected service, not scraping LinkedIn itself. Do this now
so alerts are already accumulating by the time that collector exists:

1. Run one of your target searches on linkedin.com/jobs (e.g.
   "Operations Coordinator", London)
2. Toggle **"Get notified about new jobs"** on the results page
3. Repeat for 3-4 of your top target titles

## Search priority (updated 9 Aug 2026)

Candidate wants a fast foot-in-the-door office role over one that maps
tightly to Project Management specifically — open to any office role
regardless of tier. `scoring_weights` in `job_profile.yaml` reflects
this: `entry_level_accessibility` and `salary` now outweigh
`career_transition_fit`. Revert the weights in that file if priorities
change again.

## What you need to do

1. Get a free Reed API key: https://www.reed.co.uk/developers
2. Get free Adzuna credentials: https://developer.adzuna.com
3. Copy `.env.example` to `.env` and paste them in.

`.env` is gitignored and must never be committed.

## Changing what it looks for

Edit `config/job_profile.yaml`. It is plain text with comments — target
job titles, locations, salary floor, scoring weights and alert thresholds
all live there. Run `python main.py --check-config` afterwards; it will
tell you plainly if anything is wrong.

## Design notes

**Your status edits are protected.** If you mark a job APPLIED and a later
automated run rediscovers it, the run refreshes salary and description but
leaves your status alone. Enforced by `status_source` in `app/database/db.py`
and covered by `test_automation_never_overwrites_user_status`.

**Daylight saving is handled.** GitHub Actions cron is UTC-only and does not
shift for BST. The workflow triggers at both 07:00 and 08:00 UTC, and
`app/timeutils.py` lets exactly one of them through — the one that is 08:00
in London. Covered by five tests across GMT, BST and both changeover weekends.

**Duplicates collapse across boards.** One vacancy on Reed and Adzuna
produces one job record with two source URLs. "Bright Facilities Ltd" in
Croydon, Surrey and "Bright Facilities Limited" in Croydon are recognised
as the same employer and place.

**AI spend is capped.** Deterministic filters run first, and a description
hash prevents paying to re-analyse a job whose advert has not changed.
