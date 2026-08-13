# Job Hunter — User Guide

Written assuming no programming knowledge. Every command below is typed
into Terminal, in your Job-Hunter folder.

---

## What works right now, and what still needs setting up

| Feature | State |
|---|---|
| Searching Adzuna | **Working — verified live** |
| Scoring, filtering, deduplication | **Working — 100 tests passing** |
| Local database / tracker | **Working** |
| Diagnostics and insights | **Working** |
| Searching Reed | Built, needs an API key |
| Emailing you results | Built, **never tested** — needs Gmail setup |
| Google Sheets sync | Built, **never tested** — needs Google Cloud setup |
| Daily automatic runs | Built, needs GitHub secrets |
| Dashboard | Built, needs `pip3 install -r requirements.txt` |

"Never tested" is meant literally. The environment this was built in had
no internet access, so no email has ever been sent and no spreadsheet has
ever been written. They follow Google's documented patterns, but you'll
find out for certain when you run them.

---

## Everyday use

**Get to the right folder first.** Every time you open a new Terminal
window:

```
cd
```

Type `cd`, then a space, then drag your Job-Hunter folder from Finder into
the Terminal window, then press Enter.

### The commands

```
python3 main.py --run          Search, score and save. Takes about a minute.
python3 main.py --diagnose     Explains why jobs scored as they did.
python3 main.py --digest       Shows (or emails) your top matches.
python3 main.py --insights     What your application results suggest.
python3 main.py --status       System status and build progress.
python3 main.py --dashboard    Opens the visual dashboard in a browser.
python3 main.py --test-sources Checks your job board connections.
python3 main.py --test-email   Sends a test email.
python3 main.py --update-sheets Syncs to Google Sheets.
```

Run them one at a time, pressing Enter after each and waiting for the `%`
prompt to come back before typing the next.

---

## Changing what it looks for

Everything is in **`config/job_profile.yaml`**. Open it in TextEdit. It's
plain text with comments explaining each part.

You can change:

- **Target job titles** — the `tier_1`, `tier_2`, `tier_3` lists
- **Locations** and how much each is worth (`location_score`, 0-5)
- **Minimum salary** — currently £21,000
- **Scoring weights** — must total exactly 100
- **Thresholds** — 70 to be tracked, 80 to be emailed, 90 for an urgent alert
- **Your skills** — under `candidate: skills:`

That last one matters more than it looks. The scorer matches those skills
against job adverts. **Add to it as you genuinely learn things** — put
`power bi` in once you've actually used Power BI, not before. Overstating
it here means the system recommends jobs you'd struggle in.

After any edit:

```
python3 main.py --check-config
```

It will tell you in plain English if something is wrong — for example if
your weights add up to 105 instead of 100.

---

## Adding the Reed API key

Reed is worth getting. Adzuna truncates job descriptions at 500
characters, which starves four of the seven scoring components. Reed
returns full adverts, which makes every score more accurate.

1. Go to **reed.co.uk/developers/jobseeker**
2. Near the top there is an **API key** section with a **Register** button
   — it sits above the technical documentation and is easy to scroll past
3. Once you have the key, open your `.env` file and add it:
   `REED_API_KEY=your-key-here`
4. Check it worked: `python3 main.py --test-sources`

---

## Connecting Gmail

This is the fiddliest part of the whole system — around 20-30 minutes.
Do it when you have patience, not at the end of a shift.

1. Go to **console.cloud.google.com** and sign in
2. Create a new project (top-left dropdown → New Project), name it
   anything, e.g. `job-hunter`
3. In the search bar, find **Gmail API** and click **Enable**
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**
   - Fill in app name, your email as support contact, your email as developer contact
   - On the Scopes step, click **Add or Remove Scopes**, and add
     `https://www.googleapis.com/auth/gmail.send`
   - On Test users, click **Add Users** and add **your own Gmail address**.
     Skipping this is the single most common reason this fails later.
5. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Create, then **Download JSON**
6. In Finder, make a folder called `credentials` inside Job-Hunter, and put
   the downloaded file in it, renamed to `gmail_oauth_client.json`
7. Open `.env` and set:
   ```
   GMAIL_SENDER_ADDRESS=your@gmail.com
   GMAIL_RECIPIENT_ADDRESS=your@gmail.com
   ```
8. Run `python3 main.py --test-email`

The first time, a browser window opens asking you to allow access. You
will see a warning that the app isn't verified — that's expected, because
the "app" is your own project. Click **Advanced → Go to (your project)**.

If it worked, you get a test email and a token is saved so you won't be
asked again.

**The `credentials` folder is gitignored** — it never goes to GitHub.

---

## Connecting Google Sheets

Similar effort to Gmail. Optional — the local database works without it.

1. In the same Google Cloud project, enable the **Google Sheets API**
2. **Credentials → Create Credentials → Service account**
3. Create it, then open it → **Keys → Add Key → Create new key → JSON**
4. Save that file as `credentials/service_account.json`
5. Open the downloaded JSON and find the `client_email` value — it looks
   like `something@yourproject.iam.gserviceaccount.com`
6. Create a Google Sheet, name it, and **Share it with that email address
   as an Editor**. The sync fails without this step.
7. Copy the spreadsheet ID from its URL — the long string between `/d/`
   and `/edit`
8. In `.env`: `GOOGLE_SHEETS_SPREADSHEET_ID=that-long-string`
9. Run `python3 main.py --update-sheets`

**Your Status column is protected.** If you mark a job APPLIED in the
sheet, later syncs refresh the salary, score and description but leave
your status alone.

---

## Turning on the daily automatic run

1. Push the latest code to GitHub (GitHub Desktop → Commit → Push)
2. On github.com, open your repo → **Settings** → **Secrets and variables**
   → **Actions**
3. Add each of these with **New repository secret**:
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
   - `REED_API_KEY` (when you have it)
   - `GMAIL_SENDER_ADDRESS` and `GMAIL_RECIPIENT_ADDRESS` (if using Gmail)
4. Go to the **Actions** tab. You should see "Daily job search".
5. Click **Run workflow** to test it immediately rather than waiting

**About the timing.** GitHub's scheduler only understands UTC and does not
adjust for British Summer Time. The workflow therefore runs at both 07:00
and 08:00 UTC, and the code checks the real London time and quits
immediately unless it is genuinely 08:00 there. Exactly one of the two
runs proceeds on any given day, all year round.

**Results are committed back to the repo** as `data/latest_results.md`.
GitHub will email you when it changes — a zero-setup digest, useful even
before you configure Gmail.

---

## Applying for jobs — the intended workflow

The system never applies for anything. That was deliberate from the start.

1. Read the digest, or run `--diagnose`
2. Open the advert — **read it properly.** The score comes from keyword
   matching and cannot tell a genuine "no experience needed" advert from
   one that says that and then lists five years of requirements
3. Apply, in your own words, with the right CV version
4. Record it: open the Google Sheet (or the dashboard) and set the Status
   to `APPLIED`, with the date
5. Set a follow-up date about two weeks out
6. Update the status as things move: `INTERVIEW`, `OFFER`, `REJECTED`

Step 4 matters more than it looks. The learning loop reads those statuses.
After 20 applications, `--insights` will tell you which job titles and
salary bands are actually converting — but only if you record the results.

---

## Stopping it

**Pause the daily run:** GitHub repo → **Actions** → "Daily job search" →
**...** menu → **Disable workflow**. Re-enable the same way.

**Stop the dashboard:** press **Ctrl + C** in the Terminal window.

**Stop everything permanently:** GitHub repo → Settings → scroll to the
bottom → Delete this repository. Then regenerate your Adzuna and Reed keys
in their developer dashboards so the old ones stop working.

---

## Troubleshooting

**"Nothing happens when I run it"**
`--run` makes 17 API calls and prints nothing until they finish — give it
a minute. If there's no text at all, you're probably in the wrong folder:
type `pwd` and check it ends in `Job-Hunter`.

**"command not found: python3"**
Python isn't installed. Get it from python.org/downloads.

**"No such file or directory: main.py"**
Wrong folder. `cd `, then drag the Job-Hunter folder in, then Enter.

**"It isn't finding jobs"**
Run `--test-sources` first. If Adzuna says SKIPPED, your `.env` is missing
or has no keys. If it says OK but `--run` finds nothing, your filters may
be too strict — check `minimum_salary` and the target titles in
`config/job_profile.yaml`.

**"Everything scores low"**
Expected on Adzuna-only data — it truncates descriptions at 500
characters. Run `--diagnose` to confirm; if it reports 500-character
adverts, that's the cause. Adding the Reed key is the real fix.

**"Gmail isn't sending"**
Almost always the OAuth consent screen: you must add your own email under
**Test users**. Delete `credentials/gmail_token.json` and run
`--test-email` again to redo the consent flow.

**"Google Sheets isn't updating"**
You probably didn't share the sheet with the service account email. Open
`credentials/service_account.json`, find `client_email`, and share the
sheet with that address as an Editor.

**"The script crashed"**
Copy the last few lines of red text. Most errors name the exact file and
line. If it mentions a missing module, run
`pip3 install -r requirements.txt`.

**"I want to change my target jobs"**
Edit `config/job_profile.yaml`, then run `--check-config`.

**"It's filtering out jobs I actually wanted"**
The seniority filter rejects titles containing manager, head of, director,
lead or supervisor — unless they also contain junior, assistant, trainee,
graduate, coordinator or administrator. If it's dropping something you
wanted, the lists are at the top of `app/filtering/filters.py`.

---

## What this system is not

It's a triage tool. It reads adverts for keywords and ranks them. It has
no understanding of context, company culture, or what a job is actually
like day to day.

A score of 85 means "this advert contains many of the words that matter to
you." It does not mean you'll enjoy the job, get an interview, or that the
advert is honest about its own requirements.

Read every advert before applying. The system's job is to make sure you
see the right ones, not to decide for you.
