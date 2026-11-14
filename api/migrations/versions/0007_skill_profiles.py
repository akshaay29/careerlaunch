"""Skill Profiles (FR-4, AD-8).

* ``skill_profiles`` is owned by ``profile``: one row per owner (``owner_id`` is the primary
  key, ``ON DELETE CASCADE`` from ``accounts``).
* ``resume_id`` is the Resume the extraction came from (``ON DELETE SET NULL``: deleting that
  Resume keeps the profile, which is the Student's data, not file content).
* ``status`` is ``pending | ready | unreadable | failed``, or NULL when the row exists only
  because the Student entered skills manually (shown as ``none``).
* ``extracted_skills`` (with ``vocab_version`` and ``extracted_at``) come from the extraction
  job; ``added_skills`` / ``removed_skills`` / ``custom_skills`` are the Student's edits,
  applied over the extracted set and kept across re-extractions until reset.
* ``education`` / ``experience`` are JSON arrays of plain-text entries. The raw Resume text is
  never stored.

Revision ID: 0007_skill_profiles
Revises: 0006_resumes
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0007_skill_profiles"
down_revision: str | None = "0006_resumes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.skill_profiles (
            owner_id uuid PRIMARY KEY,
            resume_id uuid,
            status text,
            extracted_skills text[] NOT NULL DEFAULT '{}',
            vocab_version text,
            added_skills text[] NOT NULL DEFAULT '{}',
            removed_skills text[] NOT NULL DEFAULT '{}',
            custom_skills text[] NOT NULL DEFAULT '{}',
            education jsonb NOT NULL DEFAULT '[]'::jsonb,
            experience jsonb NOT NULL DEFAULT '[]'::jsonb,
            extracted_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT skill_profiles_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT skill_profiles_resume_id_fkey FOREIGN KEY (resume_id)
                REFERENCES app.resumes (id) ON DELETE SET NULL,
            CONSTRAINT skill_profiles_status_check
                CHECK (status IN ('pending', 'ready', 'unreadable', 'failed')),
            CONSTRAINT skill_profiles_custom_skills_check
                CHECK (cardinality(custom_skills) <= 50),
            CONSTRAINT skill_profiles_education_check
                CHECK (jsonb_typeof(education) = 'array'),
            CONSTRAINT skill_profiles_experience_check
                CHECK (jsonb_typeof(experience) = 'array')
        )
        """
    )
    op.execute("CREATE INDEX skill_profiles_resume_id_idx ON app.skill_profiles (resume_id)")
    create_app_table_rls("skill_profiles")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.skill_profiles")
