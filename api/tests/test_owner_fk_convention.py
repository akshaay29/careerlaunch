"""AD-5 / AD-17 convention, checked against the live catalog so new tables are covered
automatically: every ``app`` table with an ``owner_id`` column cascades from ``accounts``,
and every other foreign key to ``accounts`` is CASCADE or SET NULL. A failure names the table.
"""

import psycopg

# Tables whose ``owner_id`` deliberately does NOT cascade, with the action they use instead.
# ``ops_events`` is a global, append-only log: the row survives with the owner set to NULL.
OWNER_FK_EXCEPTIONS = {"ops_events": "n"}  # n = SET NULL

_ACTIONS = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}


def _owner_tables(conn: psycopg.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            """
            SELECT c.relname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p') AND a.attname = 'owner_id'
              AND NOT a.attisdropped
            ORDER BY c.relname
            """
        )
    ]


def _account_fks(conn: psycopg.Connection) -> list[tuple[str, list[str], str]]:
    """(table, columns, on-delete action) of every FK that references ``app.accounts``."""
    return [
        (table, list(cols), action)
        for table, cols, action in conn.execute(
            """
            SELECT c.relname,
                   array_agg(a.attname::text ORDER BY k.ord),
                   con.confdeltype::text
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE con.contype = 'f' AND con.confrelid = 'app.accounts'::regclass
              AND n.nspname = 'app'
            GROUP BY con.oid, c.relname, con.confdeltype
            """
        )
    ]


def test_every_owner_id_table_cascades_from_accounts(sync_conn: psycopg.Connection) -> None:
    tables = _owner_tables(sync_conn)
    assert tables, "no owner_id tables found"
    fks = _account_fks(sync_conn)
    problems: list[str] = []
    for table in tables:
        actions = {action for t, cols, action in fks if t == table and cols == ["owner_id"]}
        expected = OWNER_FK_EXCEPTIONS.get(table, "c")
        if actions != {expected}:
            found = ", ".join(sorted(_ACTIONS[a] for a in actions)) or "no FK to accounts(id)"
            problems.append(
                f"{table}.owner_id must reference accounts(id) ON DELETE "
                f"{_ACTIONS[expected]} (found: {found})"
            )
    assert not problems, "\n".join(problems)


def test_every_fk_to_accounts_cascades_or_nulls(sync_conn: psycopg.Connection) -> None:
    problems = [
        f"{table}({', '.join(cols)}) -> accounts is ON DELETE {_ACTIONS[action]}"
        for table, cols, action in _account_fks(sync_conn)
        if action not in ("c", "n")
    ]
    assert not problems, "\n".join(problems)


def test_exceptions_are_real_tables(sync_conn: psycopg.Connection) -> None:
    """A stale allow-list entry would silently stop protecting nothing; keep it honest."""
    assert set(OWNER_FK_EXCEPTIONS) <= set(_owner_tables(sync_conn))


def test_deleted_accounts_is_a_bare_tombstone(sync_conn: psycopg.Connection) -> None:
    """The tombstone keeps only the uid and timestamps (no email or other PII), has no FK,
    and has forced RLS with no exposed grants."""
    cols = [
        r[0]
        for r in sync_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'app' AND table_name = 'deleted_accounts' ORDER BY ordinal_position"
        )
    ]
    assert cols == ["id", "deleted_at", "files_purged_at", "auth_deleted_at"]
    assert sync_conn.execute(
        "SELECT count(*) FROM pg_constraint WHERE conrelid = 'app.deleted_accounts'::regclass "
        "AND contype = 'f'"
    ).fetchone() == (0,)
    assert sync_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.deleted_accounts'::regclass"
    ).fetchone() == (True, True)
    for role in ("anon", "authenticated"):
        assert sync_conn.execute(
            "SELECT has_table_privilege(%s, 'app.deleted_accounts', 'SELECT')", (role,)
        ).fetchone() == (False,)
