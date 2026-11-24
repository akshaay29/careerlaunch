"""Stored Match Scores (FR-9, NFR-3, AD-9, Story 3.2). All three tables are owned by
``matching`` and have forced RLS.

* ``match_scores``: one row per Student and candidate Listing, unique on
  ``(owner_id, listing_id)``. ``score`` is 0-100 and ``breakdown`` the ``ScoreBreakdown`` JSON;
  the algorithm, weights and vocabulary versions are kept alongside. CASCADE from ``accounts``
  and from ``listings`` (purge, eviction and takedown delete Listings). Indexed on
  ``(owner_id, score DESC)`` for the feed and on ``listing_id`` for the relevance aggregate.
* ``match_score_missing_skills``: the breakdown's missing skills, one row each, keyed on
  ``(owner_id, listing_id, skill_id)``; CASCADE from ``match_scores`` (and so from the
  Listing) and from ``accounts``.
* ``match_state``: one row per Student. ``scores_stale_since`` is set by
  ``matching.service.mark_stale``; ``last_computed_at`` is when the last recompute started and
  ``last_candidates`` its candidate count.

Revision ID: 0017_match_scores
Revises: 0016_listing_search
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0017_match_scores"
down_revision: str | None = "0016_listing_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.match_scores (
            owner_id uuid NOT NULL,
            listing_id uuid NOT NULL,
            score smallint NOT NULL,
            breakdown jsonb NOT NULL,
            algo_version int NOT NULL,
            weights_version int NOT NULL,
            vocab_version text NOT NULL,
            computed_at timestamptz NOT NULL,
            CONSTRAINT match_scores_pkey PRIMARY KEY (owner_id, listing_id),
            CONSTRAINT match_scores_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT match_scores_listing_id_fkey FOREIGN KEY (listing_id)
                REFERENCES app.listings (id) ON DELETE CASCADE,
            CONSTRAINT match_scores_score_check CHECK (score BETWEEN 0 AND 100)
        )
        """
    )
    create_app_table_rls("match_scores")
    op.execute(
        "CREATE INDEX match_scores_owner_score_idx ON app.match_scores (owner_id, score DESC)"
    )
    op.execute("CREATE INDEX match_scores_listing_id_idx ON app.match_scores (listing_id)")

    op.execute(
        """
        CREATE TABLE app.match_score_missing_skills (
            owner_id uuid NOT NULL,
            listing_id uuid NOT NULL,
            skill_id text NOT NULL,
            CONSTRAINT match_score_missing_skills_pkey
                PRIMARY KEY (owner_id, listing_id, skill_id),
            CONSTRAINT match_score_missing_skills_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT match_score_missing_skills_score_fkey FOREIGN KEY (owner_id, listing_id)
                REFERENCES app.match_scores (owner_id, listing_id) ON DELETE CASCADE
        )
        """
    )
    create_app_table_rls("match_score_missing_skills")

    op.execute(
        """
        CREATE TABLE app.match_state (
            owner_id uuid PRIMARY KEY,
            scores_stale_since timestamptz,
            last_computed_at timestamptz,
            last_candidates int,
            CONSTRAINT match_state_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT match_state_last_candidates_check CHECK (last_candidates >= 0)
        )
        """
    )
    create_app_table_rls("match_state")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.match_state")
    op.execute("DROP TABLE IF EXISTS app.match_score_missing_skills")
    op.execute("DROP TABLE IF EXISTS app.match_scores")
