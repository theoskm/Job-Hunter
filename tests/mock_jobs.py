"""
Mock vacancies so every test runs with no network and no API keys.

MOCK_REED_1 and MOCK_ADZUNA_1 are deliberately the SAME real-world
vacancy as listed by two different boards, with the employer suffix and
location rendered differently. Dedup must collapse them into one record.
"""

from app.database.models import Job, JobSource

MOCK_REED_1 = Job(
    title="Operations Coordinator",
    company="Bright Facilities Ltd",
    location="Croydon, Surrey",
    salary_min=28000,
    salary_max=32000,
    salary_raw="£28,000 - £32,000 per annum",
    employment_type="Permanent",
    working_arrangement="hybrid",
    date_posted="2026-08-05",
    description=(
        "Coordinate daily operations, schedules and resources. Liaise with "
        "suppliers and contractors. Maintain accurate records and produce "
        "weekly reports. Confident with Excel and Outlook."
    ),
    direct_application_url="https://brightfacilities.example.com/careers/ops-coord",
    sources=[
        JobSource(
            source="reed",
            source_url="https://www.reed.co.uk/jobs/operations-coordinator/11111111",
            source_job_id="11111111",
        )
    ],
)

# Same vacancy, different board: "Limited" vs "Ltd", county dropped.
MOCK_ADZUNA_1 = Job(
    title="Operations Coordinator",
    company="Bright Facilities Limited",
    location="Croydon",
    salary_min=28000,
    salary_max=32000,
    date_posted="2026-08-05",
    description=(
        "Coordinate daily operations, schedules and resources. Liaise with "
        "suppliers and contractors. Maintain accurate records and produce "
        "weekly reports. Confident with Excel and Outlook."
    ),
    sources=[
        JobSource(
            source="adzuna",
            source_url="https://www.adzuna.co.uk/jobs/details/2222222",
            source_job_id="2222222",
        )
    ],
)

MOCK_REED_2 = Job(
    title="Project Administrator",
    company="Northgate Construction",
    location="Sutton, Surrey",
    salary_min=26000,
    salary_max=29000,
    date_posted="2026-08-07",
    description=(
        "Support project managers with documentation, scheduling and "
        "purchase orders. Training provided; no prior project experience "
        "required."
    ),
    sources=[
        JobSource(
            source="reed",
            source_url="https://www.reed.co.uk/jobs/project-administrator/33333333",
            source_job_id="33333333",
        )
    ],
)

# Should be filtered out: commission-only and below the salary floor.
MOCK_UNSUITABLE = Job(
    title="Sales Executive",
    company="QuickWin Sales",
    location="London",
    salary_min=0,
    salary_raw="Commission only, OTE £60,000",
    date_posted="2026-08-01",
    description="Commission only role. Cold calling. Door to door sales.",
    sources=[
        JobSource(
            source="adzuna",
            source_url="https://www.adzuna.co.uk/jobs/details/4444444",
            source_job_id="4444444",
        )
    ],
)

ALL_MOCKS = [MOCK_REED_1, MOCK_ADZUNA_1, MOCK_REED_2, MOCK_UNSUITABLE]
