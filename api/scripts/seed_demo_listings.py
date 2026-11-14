"""seed-demo-listings: put about 30 fake demo Listings in the shared pool (LOCAL ONLY).

Usage (from ``api/``)::

    uv run python scripts/seed_demo_listings.py

The drafts go through the real ingest pipeline (``listings.service.collect_and_ingest``)
under the System collector (``collected_by`` NULL, contributes to the pool), as Source
``demo`` (label "Demo data", created or relabelled by this script), so ``/listings`` can be
checked locally and fake Listings never carry a real job board's name. Idempotent: the
drafts have fixed ``source_job_id`` values (``demo-NNN``), so running it again refreshes the
same rows instead of adding new ones. The companies are invented; "Apply" opens a LinkedIn
job search for the title. Runs only with ENVIRONMENT=local or ci. Uses DATABASE_URL via
Settings, like the API.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.modules.listings.service import (  # noqa: E402
    collect_and_ingest,
    ensure_source,
    source_state,
)
from app.ports.source_adapter import ListingDraft, SourceQuery  # noqa: E402

SOURCE = "demo"
SOURCE_LABEL = "Demo data"
ALLOWED_ENVIRONMENTS = ("local", "ci")

# (title, company, location, job type, description)
_DEMO: tuple[tuple[str, str, str, str, str], ...] = (
    ("Backend Engineering Intern", "Northwind Labs", "Bengaluru, Karnataka, India", "internship",
     "Build REST APIs in Python with FastAPI and PostgreSQL. Write unit tests with pytest and "
     "ship with Docker and GitHub Actions."),
    ("Software Engineer, New Grad", "Bluefin Systems", "Hyderabad, Telangana, India", "full_time",
     "Work on distributed systems in Java and Spring Boot. Strong data structures and "
     "algorithms, Kafka and AWS experience is a plus."),
    ("Frontend Developer Intern", "Pixelcraft Studio", "Remote", "internship",
     "React and TypeScript single-page apps, Tailwind CSS, accessibility, and Jest tests. "
     "Figma handoff with designers."),
    ("Data Analyst Intern", "Ledgerline Finance", "Mumbai, Maharashtra, India", "internship",
     "SQL, Microsoft Excel and Power BI dashboards. Python with pandas for data analysis and "
     "statistics; clear communication with stakeholders."),
    ("Machine Learning Intern", "Cortex Health AI", "Bengaluru, Karnataka, India", "internship",
     "Train models with PyTorch and scikit-learn, computer vision on medical images, NumPy, "
     "Jupyter notebooks and MLOps basics."),
    ("Full Stack Developer", "Orbit Commerce", "Pune, Maharashtra, India", "full_time",
     "Node.js and Express APIs, React frontend, MongoDB and Redis. Microservices, Docker and "
     "Kubernetes on Google Cloud."),
    ("Android Developer Intern", "Tapwise Mobile", "Chennai, Tamil Nadu, India", "internship",
     "Kotlin, Jetpack Compose and the Android SDK. Firebase, REST APIs and Git."),
    ("DevOps Engineer, Graduate", "Stratus Cloudworks", "Gurugram, Haryana, India", "full_time",
     "Linux, Terraform, Ansible and Jenkins pipelines. AWS EC2 and S3, Prometheus and Grafana "
     "monitoring, Bash scripting."),
    ("Data Engineering Intern", "Riverbed Analytics", "Remote", "internship",
     "ETL pipelines with Apache Airflow and Apache Spark (PySpark), BigQuery and Snowflake. "
     "Python and SQL."),
    ("iOS Developer", "Lumen Apps", "Bengaluru, Karnataka, India", "full_time",
     "Swift and SwiftUI apps, iOS development best practices, unit testing, CI/CD."),
    ("QA Automation Intern", "Testbridge Software", "Noida, Uttar Pradesh, India", "internship",
     "Selenium and Cypress end-to-end tests, Postman API tests, Jira, Agile scrum ceremonies."),
    ("Cloud Support Associate", "Nimbus Hosting", "Hyderabad, Telangana, India", "full_time",
     "Customer-facing troubleshooting on Microsoft Azure and Linux, networking basics, "
     "documentation and communication skills."),
    ("Product Design Intern", "Canvasly", "Remote", "internship",
     "UI/UX design in Figma, user research, prototyping and design systems. Attention to "
     "detail and creativity."),
    ("Embedded Systems Intern", "Voltgrid Devices", "Pune, Maharashtra, India", "internship",
     "Embedded C on ARM microcontrollers, IoT sensors, RTOS concepts and Linux drivers."),
    ("Security Analyst, Graduate", "Shieldpoint", "Bengaluru, Karnataka, India", "full_time",
     "Cybersecurity monitoring, network security, incident response, Python scripting and "
     "cryptography fundamentals."),
    ("Business Intelligence Intern", "Marketpulse", "Delhi, India", "internship",
     "Tableau and Power BI dashboards, SQL queries, data visualization and storytelling."),
    ("Junior Python Developer", "Fernhill Tech", "Kochi, Kerala, India", "full_time",
     "Django and Django REST framework, PostgreSQL, Celery and Redis, Docker."),
    ("NLP Research Intern", "Lexica Labs", "Remote", "internship",
     "Natural language processing with Hugging Face transformers, large language models, "
     "prompt engineering and LangChain prototypes."),
    ("Site Reliability Engineer, New Grad", "Beacon Payments", "Bengaluru, Karnataka, India",
     "full_time",
     "Go and Python services, Kubernetes, observability with Prometheus, on-call and "
     "incident reviews."),
    ("Game Developer Intern", "Polyforge Games", "Hyderabad, Telangana, India", "internship",
     "Unity and C# gameplay programming, physics, optimisation and teamwork."),
    ("Software Development Engineer I", "Cartwheel Retail", "Chennai, Tamil Nadu, India",
     "full_time",
     "Java, microservices, DynamoDB and AWS Lambda. System design and object-oriented "
     "programming."),
    ("Web Developer Intern", "Brightleaf Media", "Jaipur, Rajasthan, India", "internship",
     "HTML, CSS, JavaScript and WordPress sites; Next.js projects and Vercel deploys."),
    ("Data Scientist, Entry Level", "Quantive Insurance", "Mumbai, Maharashtra, India",
     "full_time",
     "Machine learning with XGBoost and scikit-learn, statistics, SQL and Python. Present "
     "findings to business teams."),
    ("Blockchain Developer Intern", "Chainmint", "Remote", "internship",
     "Solidity smart contracts, Ethereum tooling, JavaScript and Node.js."),
    ("Systems Engineer Trainee", "Infracore Services", "Mysuru, Karnataka, India", "full_time",
     "C++ and Linux systems programming, operating systems, computer networks and "
     "multithreading."),
    ("Research Intern, Computer Vision", "Opticept", "Bengaluru, Karnataka, India",
     "internship",
     "OpenCV and PyTorch for detection and segmentation, deep learning, research papers."),
    ("Associate Software Engineer", "Tidewater Logistics", "Ahmedabad, Gujarat, India",
     "full_time",
     "C# and .NET Core APIs, Microsoft SQL Server, Angular frontend, Agile delivery."),
    ("Platform Engineering Intern", "Keystone Cloud", "Remote", "internship",
     "Infrastructure as code with Terraform, GitHub Actions, Docker and Kubernetes."),
    ("Mobile Developer (Flutter)", "Sprout Fintech", "Pune, Maharashtra, India", "full_time",
     "Flutter and Dart cross-platform apps, Firebase, REST APIs and CI/CD."),
    ("Technical Writer Intern", "Docsmith", "Remote", "internship",
     "Technical writing and documentation for developer APIs, Markdown, Git and attention "
     "to detail."),
)


def demo_drafts(today: date | None = None) -> list[ListingDraft]:
    """The demo drafts, oldest first, with dates relative to ``today``."""
    today = today or datetime.now(UTC).date()
    drafts: list[ListingDraft] = []
    total = len(_DEMO)
    for index, (title, company, location, job_type, description) in enumerate(_DEMO):
        age_days = total - index  # later entries are newer
        posted = datetime.combine(today - timedelta(days=age_days), datetime.min.time(), UTC)
        deadline = today + timedelta(days=14 + index) if index % 3 == 0 else None
        drafts.append(
            ListingDraft(
                source=SOURCE,
                source_job_id=f"demo-{index + 1:03d}",
                url=f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(title)}",
                title=title,
                company_raw=company,
                location_raw=location,
                job_type=job_type,  # type: ignore[arg-type]
                description=description,
                posted_at=posted.replace(hour=9),
                deadline=deadline,
            )
        )
    return drafts


class DemoSource:
    """A SourceAdapter that yields :func:`demo_drafts`."""

    source = SOURCE

    async def collect(self, query: SourceQuery) -> AsyncIterator[ListingDraft]:
        for draft in demo_drafts()[: query.limit]:
            yield draft


async def run() -> int:
    """Ingest the demo drafts; returns the process exit code."""
    async with get_sessionmaker()() as session:
        await ensure_source(session, SOURCE, SOURCE_LABEL)
        count = await collect_and_ingest(
            session, DemoSource(), SourceQuery(limit=len(_DEMO)), collected_by=None
        )
        state = await source_state(session, SOURCE)
        await session.commit()
    if count == 0:
        if state == "missing":
            print(f"seed-demo-listings: the {SOURCE!r} Source does not exist; nothing ingested")
        else:
            print(f"seed-demo-listings: the {SOURCE!r} Source is disabled; nothing ingested")
        return 1
    print(f"seed-demo-listings: {count} demo Listings are in the shared pool")
    return 0


async def _main() -> int:
    try:
        return await run()
    finally:
        await dispose_engine()


def main() -> int:
    environment = get_settings().environment
    if environment not in ALLOWED_ENVIRONMENTS:
        print(
            f"seed-demo-listings: refusing to seed fake Listings with ENVIRONMENT={environment}"
            " (local or ci only)",
            file=sys.stderr,
        )
        return 2
    # psycopg's async driver cannot use Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_main(), loop_factory=loop_factory)


if __name__ == "__main__":
    raise SystemExit(main())
