"""
Phase 10 — dashboard.

Run with:  python3 main.py --dashboard
(which shells out to: streamlit run app/dashboard/app.py)

Read-only. Nothing here changes your data — use the Google Sheet or the
CLI to update statuses.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.analytics.stats import gather_stats, learning_loop
from config.settings import Settings


@st.cache_data(ttl=60)
def load(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY score DESC")]
    stats = gather_stats(conn)
    insights = learning_loop(conn)
    conn.close()
    return rows, stats, insights


def main() -> None:
    st.set_page_config(page_title="Job Hunter", page_icon="🎯", layout="wide")
    st.title("Job Hunter")

    settings = Settings.load()
    db_path = Path(settings.secrets.database_path)

    if not db_path.exists():
        st.warning("No database yet. Run `python3 main.py --run` first.")
        return

    rows, stats, insights = load(str(db_path))

    if not rows:
        st.info("No jobs tracked yet. Run `python3 main.py --run`.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Jobs tracked", stats.total_jobs)
    c2.metric("Scoring 80+", stats.scoring_80_plus)
    c3.metric("Applied", stats.applied)
    c4.metric("Interviews", stats.interviews)
    rate = stats.interview_rate
    c5.metric("Interview rate", f"{rate:.0%}" if rate is not None else "—")

    st.divider()

    left, right = st.columns([2, 1])

    with left:
        st.subheader("Tracked jobs")
        min_score = st.slider("Minimum score", 0, 100, 0, 5)
        statuses = sorted({r["status"] for r in rows})
        chosen = st.multiselect("Status", statuses, default=statuses)

        visible = [
            r for r in rows
            if (r["score"] or 0) >= min_score and r["status"] in chosen
        ]
        st.caption(f"{len(visible)} of {len(rows)} jobs")

        for row in visible[:60]:
            score = row["score"] or 0
            with st.expander(
                f"{score:>3} · {row['title']} — {row['company']} · {row['status']}"
            ):
                a, b = st.columns(2)
                a.write(f"**Location** {row['location'] or 'not stated'}")
                a.write(f"**Posted** {row['date_posted'] or 'not stated'}")
                salary = row["salary_max"] or row["salary_min"]
                a.write(f"**Salary** {f'£{salary:,.0f}' if salary else 'not stated'}")
                b.write(f"**Priority** {row['priority'] or '—'}")
                b.write(
                    f"**Breakdown** fit {row['career_fit']} · "
                    f"transferable {row['transferable_skills']} · "
                    f"access {row['accessibility']} · skills {row['skills_match']}"
                )
                if row["reasoning"]:
                    st.caption(row["reasoning"])
                if row["direct_application_url"]:
                    st.markdown(f"[Open the advert]({row['direct_application_url']})")

    with right:
        st.subheader("Pipeline")
        for status, count in sorted(stats.by_status.items(), key=lambda kv: -kv[1]):
            st.write(f"**{status}** — {count}")

        st.subheader("Common skill gaps")
        if stats.common_skill_gaps:
            for gap, count in stats.common_skill_gaps:
                st.write(f"{gap} — {count}")
        else:
            st.caption("None recorded yet.")

        st.subheader("What the data suggests")
        for insight in insights:
            st.info(f"**{insight.message}**\n\n{insight.evidence}\n\n*{insight.suggested_action}*")


if __name__ == "__main__":
    main()
