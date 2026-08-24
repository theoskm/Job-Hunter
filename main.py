#!/usr/bin/env python3
"""
Job Hunter — command line entry point.

Working today (Phases 2-3):
    python main.py --status         Show configuration and readiness
    python main.py --check-config   Validate config/job_profile.yaml

Coming in later phases (they tell you so rather than failing quietly):
    --run --digest --test-email --test-sources --update-sheets --dashboard
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.collectors.run import build_collectors
from app.database.db import Database
from app.analytics.diagnose import describe_scores
from app.analytics.stats import gather_stats, learning_loop
from app.notifications.templates import build_digest, build_single_alert
from app.pipeline import run_full_pipeline
from app.timeutils import is_scheduled_run_time, local_now, utc_offset_hours
from config.settings import ConfigError, Settings

NOT_BUILT_YET: dict[str, str] = {}


def cmd_check_config(settings: Settings) -> int:
    p = settings.profile
    print("Configuration is valid.\n")
    print(f"  Target roles     : {len(p.all_target_roles)} across 3 tiers")
    print(f"  Locations        : {', '.join(l['name'] for l in p.locations)}")
    print(f"  Scoring weights  : total {sum(p.scoring_weights.values())}/100")
    print(f"  Save to tracker  : score >= {p.thresholds['save_to_tracker']}")
    print(f"  Email alert      : score >= {p.thresholds['email_alert']}")
    print(f"  Urgent alert     : score >= {p.thresholds['urgent_alert']}")
    print(f"  Minimum salary   : £{p.minimum_salary:,}")
    print(f"  Search windows   : {p.search_windows} days")
    print(f"  Scheduled for    : {p.run_at_local_time} {p.timezone}")
    return 0


def cmd_status(settings: Settings) -> int:
    p, s = settings.profile, settings.secrets
    now = datetime.now(timezone.utc)

    print("=" * 58)
    print("JOB HUNTER — SYSTEM STATUS")
    print("=" * 58)

    print(f"\nTime now (UK)   : {local_now(p.timezone):%Y-%m-%d %H:%M %Z}")
    print(f"UTC offset      : +{utc_offset_hours(now):.0f}h "
          f"({'BST' if utc_offset_hours(now) else 'GMT'})")
    print(f"Scheduled window: {'OPEN' if is_scheduled_run_time(now) else 'closed'} "
          f"(target {p.run_at_local_time} {p.timezone})")

    print("\nCREDENTIALS")
    readiness = s.source_readiness()
    for name, ready in readiness.items():
        print(f"  {name:<10} {'READY' if ready else 'MISSING KEY — see .env.example'}")
    for label, value in (
        ("anthropic", s.anthropic_api_key),
        ("gmail", s.gmail_sender),
        ("sheets", s.sheets_spreadsheet_id),
    ):
        print(f"  {label:<10} {'configured' if value else 'not configured yet'}")

    print("\nDATABASE")
    db_path = Path(s.database_path)
    if db_path.exists():
        with Database(db_path) as db:
            counts = db.counts_by_status()
        total = sum(counts.values())
        print(f"  {db_path} — {total} job(s)")
        for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {status:<18} {n}")
    else:
        print(f"  Not created yet (will appear at {db_path} on first run)")

    print("\nBUILD PROGRESS")
    print("  Phase 1  Connection audit          COMPLETE")
    print("  Phase 2  Architecture & config     COMPLETE")
    print("  Phase 3  Database                  COMPLETE")
    print("  Phase 4  Collectors                not built")
    print("  Phase 5  Filtering & dedup         partial (dedup key done)")
    print("  Phase 6  Scoring engine            not built")
    print("  Phase 7+ Sheets, Gmail, dashboard  not built")
    print()
    return 0


def cmd_run(settings: Settings) -> int:
    print("Running the full search — this hits the real APIs and may take a minute.\n")
    db_path = Path(settings.secrets.database_path)
    with Database(db_path) as db:
        db.init_schema()
        report = run_full_pipeline(settings, db)

    print(f"  Raw results found     : {report.raw_found}")
    print(f"  Search window used    : {report.window_days_used} days"
          + (" (widened — 7-day pass found too few)" if report.window_was_widened else ""))
    print(f"  Filtered out          : {report.filtered_out}  (salary floor / excluded words / too old)")
    print(f"  Duplicates merged     : {report.duplicates_merged}")
    print(f"  Scored                : {report.scored}")
    print(f"  New in database       : {report.new_jobs}")
    print(f"  Existing, refreshed   : {report.updated_jobs}")
    print(f"  Scoring >= {settings.profile.thresholds['save_to_tracker']} (tracked): {report.saved_to_tracker}")

    if report.source_errors:
        print(f"\n  {len(report.source_errors)} quer(y/ies) failed and were skipped:")
        for err in report.source_errors[:5]:
            print(f"    - {err}")
        if len(report.source_errors) > 5:
            print(f"    ... and {len(report.source_errors) - 5} more")

    if report.top_matches:
        print("\nTop matches this run:")
        for job in report.top_matches:
            print(f"  {job.score:>3}  {job.priority:<10} {job.title} — {job.company}")
    elif report.scored == 0:
        print("\nNo sources returned results — check REED_API_KEY / ADZUNA_APP_ID / "
              "ADZUNA_APP_KEY in .env, or run --test-sources to check credentials.")
    else:
        print(f"\nNothing scored {settings.profile.thresholds['save_to_tracker']}+ this run. "
              "That's a real result, not a bug — check the database for the full list.")

    return 0


def cmd_test_sources(settings: Settings) -> int:
    print("Testing each configured job source with one real, live query.\n")
    collectors = build_collectors(settings.secrets)
    overall_ok = True

    for name, collector in collectors.items():
        if not collector.is_configured:
            print(f"  {name:<8} SKIPPED — no credentials in .env")
            continue
        try:
            jobs = collector.search(keywords="Administrator", location="Croydon")
            print(f"  {name:<8} OK — {len(jobs)} result(s) for a test 'Administrator' search")
        except Exception as exc:  # noqa: BLE001 — deliberately broad: this is a diagnostic
            print(f"  {name:<8} FAILED — {exc}")
            overall_ok = False

    print()
    if overall_ok:
        print("All configured sources responded. Nothing configured is not a failure —")
        print("add REED_API_KEY to .env when you have it.")
    else:
        print("At least one configured source failed — check the message above against .env.")
    return 0 if overall_ok else 1


def cmd_diagnose(settings: Settings) -> int:
    """Shows WHY jobs scored as they did — which component is dragging."""
    db_path = Path(settings.secrets.database_path)
    if not db_path.exists():
        print("No database yet — run `python3 main.py --run` first.")
        return 1
    with Database(db_path) as db:
        rows = db.conn.execute(
            "SELECT * FROM jobs WHERE score IS NOT NULL ORDER BY score DESC"
        ).fetchall()
    print(describe_scores(rows, settings.profile))
    return 0



def _email_settings(settings: Settings) -> tuple[str, str, str, str]:
    s = settings.secrets
    return (s.gmail_sender, s.gmail_recipient,
            s.google_oauth_client_secrets, s.google_token_path)


def cmd_test_email(settings: Settings) -> int:
    """Sends one real email. UNVERIFIED until this succeeds for you."""
    from app.notifications.gmail import GmailNotConfigured, send_email
    from app.notifications.templates import Email

    sender, recipient, secrets_path, token_path = _email_settings(settings)
    email = Email(
        subject="Job Hunter — test email",
        body_text="If you are reading this, Gmail is correctly configured.",
        body_html="<p>If you are reading this, Gmail is correctly configured.</p>",
    )
    try:
        message_id = send_email(email, sender, recipient, secrets_path, token_path)
    except GmailNotConfigured as exc:
        print(f"Gmail is not set up yet:\n\n  {exc}\n")
        print("See USER_GUIDE.md, section 'Connecting Gmail'.")
        return 1
    except Exception as exc:  # noqa: BLE001 — diagnostic
        print(f"Send failed: {exc}")
        return 1
    print(f"Sent. Gmail message id: {message_id}")
    print(f"Check the inbox for {recipient}.")
    return 0


def cmd_digest(settings: Settings) -> int:
    """Builds the daily digest, and sends it if Gmail is configured."""
    db_path = Path(settings.secrets.database_path)
    if not db_path.exists():
        print("No database yet — run `python3 main.py --run` first.")
        return 1

    threshold = settings.profile.thresholds["save_to_tracker"]
    with Database(db_path) as db:
        rows = db.jobs_above_score(threshold)
        stats = gather_stats(db.conn)
        new_count = len(db.jobs_needing_alert(threshold))

    email = build_digest(rows, new_count=new_count, stats=stats)

    sender, recipient, secrets_path, token_path = _email_settings(settings)
    if not sender or not recipient:
        print("Gmail is not configured, so here is the digest instead:\n")
        print(email.body_text)
        print("\nTo have this emailed to you, see USER_GUIDE.md, 'Connecting Gmail'.")
        return 0

    from app.notifications.gmail import GmailNotConfigured, send_email

    try:
        send_email(email, sender, recipient, secrets_path, token_path)
    except (GmailNotConfigured, Exception) as exc:  # noqa: BLE001
        print(f"Could not send the digest ({exc}). Printing it instead:\n")
        print(email.body_text)
        return 1
    print(f"Digest sent to {recipient} — {new_count} new match(es).")
    return 0


def cmd_update_sheets(settings: Settings) -> int:
    """Syncs the tracker to Google Sheets. UNVERIFIED until it works for you."""
    from app.sheets.sync import SheetsNotConfigured, sync_to_sheet

    db_path = Path(settings.secrets.database_path)
    if not db_path.exists():
        print("No database yet — run `python3 main.py --run` first.")
        return 1

    try:
        with Database(db_path) as db:
            written = sync_to_sheet(
                db.conn,
                spreadsheet_id=settings.secrets.sheets_spreadsheet_id,
                service_account_json=settings.secrets.service_account_json,
            )
    except SheetsNotConfigured as exc:
        print(f"Google Sheets is not set up yet:\n\n  {exc}\n")
        print("See USER_GUIDE.md, section 'Connecting Google Sheets'.")
        return 1
    except Exception as exc:  # noqa: BLE001 — diagnostic
        print(f"Sync failed: {exc}")
        return 1
    print(f"Synced {written} row(s) to Google Sheets.")
    return 0


def cmd_dashboard(settings: Settings) -> int:
    import subprocess

    target = Path(__file__).resolve().parent / "app" / "dashboard" / "app.py"
    print("Starting the dashboard — it will open in your browser.")
    print("Press Ctrl+C in this window to stop it.\n")
    try:
        subprocess.run(["streamlit", "run", str(target)], check=False)
    except FileNotFoundError:
        print("Streamlit is not installed — run: pip3 install -r requirements.txt")
        return 1
    return 0


def cmd_insights(settings: Settings) -> int:
    """The learning loop from Section 21."""
    db_path = Path(settings.secrets.database_path)
    if not db_path.exists():
        print("No database yet — run `python3 main.py --run` first.")
        return 1
    with Database(db_path) as db:
        insights = learning_loop(db.conn)
    for insight in insights:
        print(f"\n  {insight.message}")
        print(f"    Evidence: {insight.evidence}")
        print(f"    Suggested: {insight.suggested_action}")
    print()
    return 0


def cmd_init_db(settings: Settings) -> int:
    with Database(settings.secrets.database_path) as db:
        db.init_schema()
    print(f"Database ready at {settings.secrets.database_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="main.py", description="Personal automated job-hunting system"
    )
    for flag, help_text in (
        ("--run", "Run a full job search"),
        ("--digest", "Send the daily digest"),
        ("--test-email", "Send a test email"),
        ("--test-sources", "Check every job source responds"),
        ("--update-sheets", "Sync the CRM to Google Sheets"),
        ("--dashboard", "Launch the dashboard"),
        ("--diagnose", "Explain why jobs scored as they did"),
        ("--insights", "What your application results suggest"),
        ("--scheduled", "Only run if it is 08:00 UK time (for GitHub Actions)"),
        ("--status", "Show system status"),
        ("--check-config", "Validate your job profile"),
        ("--init-db", "Create the database"),
    ):
        parser.add_argument(flag, action="store_true", help=help_text)

    args = parser.parse_args(argv)

    # --scheduled is a modifier, not a command. GitHub Actions fires this
    # workflow at both 07:00 and 08:00 UTC; exactly one of those is 08:00
    # in London depending on BST, and this gate lets only that one through.
    scheduled = getattr(args, "scheduled", False)
    if scheduled:
        now = datetime.now(timezone.utc)
        try:
            profile_for_time = Settings.load().profile
            target = profile_for_time.run_at_local_time
            tz = profile_for_time.timezone
        except ConfigError:
            target, tz = "08:00", "Europe/London"
        if not is_scheduled_run_time(now, target, tz):
            local = local_now(tz)
            print(f"Not the scheduled hour ({local:%H:%M %Z}, target {target}) — exiting.")
            return 0
        args.scheduled = False

    chosen = [k for k, v in vars(args).items() if v]
    if not chosen:
        parser.print_help()
        return 0

    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n\n  {exc}\n", file=sys.stderr)
        return 1

    command = chosen[0]
    if command in NOT_BUILT_YET:
        print(f"'--{command.replace('_', '-')}' is not built yet — "
              f"it arrives in {NOT_BUILT_YET[command]}.")
        print("Run 'python main.py --status' to see build progress.")
        return 2

    return {
        "status": cmd_status,
        "check_config": cmd_check_config,
        "init_db": cmd_init_db,
        "test_sources": cmd_test_sources,
        "run": cmd_run,
        "diagnose": cmd_diagnose,
        "insights": cmd_insights,
        "digest": cmd_digest,
        "test_email": cmd_test_email,
        "update_sheets": cmd_update_sheets,
        "dashboard": cmd_dashboard,
    }[command](settings)


if __name__ == "__main__":
    raise SystemExit(main())
