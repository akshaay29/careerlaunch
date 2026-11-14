"""AD-7 convention (Story 2.5), checked against the live catalog so new tables are covered
automatically: purge, eviction and takedown delete Listings, so every foreign key INTO
``app.listings`` must be ON DELETE CASCADE (score tables) or SET NULL (``applications.listing_id``).
A RESTRICT / NO ACTION key would make those deletes fail."""

import psycopg

_ACTIONS = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}


def listing_fk_problems(conn: psycopg.Connection) -> list[str]:
    """One message per foreign key into ``app.listings`` that is not CASCADE or SET NULL."""
    problems = []
    for table, name, action in conn.execute(
        """
        SELECT n.nspname || '.' || c.relname, con.conname, con.confdeltype::text
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = 'app.listings'::regclass
        ORDER BY 1, con.conname
        """
    ):
        if action not in ("c", "n"):
            problems.append(
                f"{table}.{name} references app.listings ON DELETE {_ACTIONS[action]}: purge, "
                "eviction and takedown delete Listings, so every foreign key into listings "
                "must be ON DELETE CASCADE (score tables) or SET NULL (applications.listing_id)"
            )
    return problems


def test_every_fk_into_listings_cascades_or_nulls(sync_conn: psycopg.Connection) -> None:
    problems = listing_fk_problems(sync_conn)
    assert not problems, "\n".join(problems)


def test_the_check_catches_a_restrict_key(sync_conn: psycopg.Connection) -> None:
    """The checker is not vacuous: a RESTRICT key (rolled back with the connection) is
    reported, with its table named; CASCADE and SET NULL keys are not."""
    sync_conn.execute(
        "CREATE TABLE app.fk_probe_restrict (listing_id uuid REFERENCES app.listings (id) "
        "ON DELETE RESTRICT)"
    )
    sync_conn.execute(
        "CREATE TABLE app.fk_probe_noaction (listing_id uuid REFERENCES app.listings (id))"
    )
    sync_conn.execute(
        "CREATE TABLE app.fk_probe_cascade (listing_id uuid REFERENCES app.listings (id) "
        "ON DELETE CASCADE)"
    )
    sync_conn.execute(
        "CREATE TABLE app.fk_probe_null (listing_id uuid REFERENCES app.listings (id) "
        "ON DELETE SET NULL)"
    )

    problems = listing_fk_problems(sync_conn)

    assert len(problems) == 2
    assert "fk_probe_restrict" in problems[1] and "RESTRICT" in problems[1]
    assert "fk_probe_noaction" in problems[0] and "NO ACTION" in problems[0]
    assert "must be ON DELETE CASCADE" in problems[0]


def test_listing_blocks_is_global(sync_conn: psycopg.Connection) -> None:
    """The takedown table has no owner_id, a forced RLS and no exposed grants."""
    cols = {
        r[0]
        for r in sync_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'app' AND table_name = 'listing_blocks'"
        )
    }
    assert cols == {"id", "kind", "source", "value", "created_at"}
    assert sync_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.listing_blocks'::regclass"
    ).fetchone() == (True, True)
    for role in ("anon", "authenticated"):
        assert sync_conn.execute(
            "SELECT has_table_privilege(%s, 'app.listing_blocks', 'SELECT')", (role,)
        ).fetchone() == (False,)
