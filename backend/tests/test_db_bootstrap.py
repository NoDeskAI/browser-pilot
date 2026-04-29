import asyncio

from app import db


class FakePool:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def reset_db_state():
    db._pool = None
    db._set_bootstrap_state(
        "waiting_database",
        current_revision="",
        target_revision="",
        pending_revisions=[],
        error="",
        attempt=0,
    )


def test_pending_revisions_are_ordered_from_current_to_head():
    script = db._script_directory(db._alembic_config())

    assert db._pending_revisions(script, "0007", "0009") == ["0008", "0009"]
    assert db._pending_revisions(script, "0009", "0009") == []


def test_upgrade_skips_alembic_when_revision_is_current(monkeypatch):
    reset_db_state()
    monkeypatch.setattr(
        db,
        "_collect_migration_info",
        lambda _connection: db.MigrationInfo(
            current_revision="0009",
            target_revision="0009",
            pending_revisions=[],
            current_revision_after="0009",
        ),
    )

    def fail_upgrade(*_args, **_kwargs):
        raise AssertionError("upgrade should not run when DB is already at head")

    monkeypatch.setattr(db.command, "upgrade", fail_upgrade)

    info = db._upgrade_with_connection(object())

    assert info.current_revision_after == "0009"


def test_upgrade_failure_preserves_revision_context(monkeypatch):
    reset_db_state()
    monkeypatch.setattr(
        db,
        "_collect_migration_info",
        lambda _connection: db.MigrationInfo(
            current_revision="0008",
            target_revision="0009",
            pending_revisions=["0009"],
        ),
    )

    def fail_upgrade(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(db.command, "upgrade", fail_upgrade)

    try:
        db._upgrade_with_connection(object())
    except db.MigrationExecutionError as exc:
        assert str(exc) == "boom"
        assert exc.info.current_revision == "0008"
        assert exc.info.target_revision == "0009"
        assert exc.info.pending_revisions == ["0009"]
    else:
        raise AssertionError("expected MigrationExecutionError")


def test_attempt_init_marks_ready_after_successful_migration(monkeypatch):
    reset_db_state()
    pool = FakePool()

    async def fake_run_migrations(_database_url):
        return db.MigrationInfo(
            current_revision="0008",
            target_revision="0009",
            pending_revisions=["0009"],
            current_revision_after="0009",
        )

    async def fake_create_pool(*_args, **_kwargs):
        return pool

    monkeypatch.setattr(db, "_run_migrations", fake_run_migrations)
    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)

    result = asyncio.run(db._attempt_init("postgresql://user:pass@localhost/db", 1))
    state = db.get_bootstrap_state()

    assert result == "ready"
    assert db.is_ready()
    assert db._pool is pool
    assert state["status"] == "ready"
    assert state["currentRevision"] == "0009"
    assert state["targetRevision"] == "0009"
    assert state["pendingRevisions"] == []


def test_attempt_init_blocks_on_incompatible_schema(monkeypatch):
    reset_db_state()

    async def fake_run_migrations(_database_url):
        info = db.MigrationInfo(current_revision="0010", target_revision="0009")
        raise db.IncompatibleSchemaError("future schema", info)

    monkeypatch.setattr(db, "_run_migrations", fake_run_migrations)

    result = asyncio.run(db._attempt_init("postgresql://user:pass@localhost/db", 1))
    state = db.get_bootstrap_state()

    assert result == "blocked"
    assert not db.is_ready()
    assert state["status"] == "incompatible_schema"
    assert state["currentRevision"] == "0010"
    assert state["targetRevision"] == "0009"
    assert state["error"] == "future schema"


def test_attempt_init_blocks_on_migration_failure(monkeypatch):
    reset_db_state()

    async def fake_run_migrations(_database_url):
        info = db.MigrationInfo(current_revision="0008", target_revision="0009", pending_revisions=["0009"])
        raise db.MigrationExecutionError("migration failed", info)

    monkeypatch.setattr(db, "_run_migrations", fake_run_migrations)

    result = asyncio.run(db._attempt_init("postgresql://user:pass@localhost/db", 1))
    state = db.get_bootstrap_state()

    assert result == "blocked"
    assert not db.is_ready()
    assert state["status"] == "migration_failed"
    assert state["pendingRevisions"] == ["0009"]
    assert state["error"] == "migration failed"


def test_attempt_init_retries_when_database_is_unavailable(monkeypatch):
    reset_db_state()

    async def fake_run_migrations(_database_url):
        raise OSError("database unavailable")

    monkeypatch.setattr(db, "_run_migrations", fake_run_migrations)

    result = asyncio.run(db._attempt_init("postgresql://user:pass@localhost/db", 2))
    state = db.get_bootstrap_state()

    assert result == "retry"
    assert not db.is_ready()
    assert state["status"] == "waiting_database"
    assert state["attempt"] == 2
    assert state["error"] == "database unavailable"


def test_run_migrations_wraps_upgrade_in_advisory_lock(monkeypatch):
    reset_db_state()
    calls = []

    class FakeConnection:
        async def execute(self, statement, params):
            calls.append((str(statement), params))

        async def commit(self):
            calls.append(("commit", {}))

        async def run_sync(self, _fn):
            return db.MigrationInfo(
                current_revision="0008",
                target_revision="0009",
                pending_revisions=["0009"],
                current_revision_after="0009",
            )

    class FakeConnectContext:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConnectContext()

        async def dispose(self):
            calls.append(("dispose", {}))

    monkeypatch.setattr(db, "create_async_engine", lambda *_args, **_kwargs: FakeEngine())

    info = asyncio.run(db._run_migrations("postgresql://user:pass@localhost/db"))

    assert info.current_revision_after == "0009"
    assert "pg_advisory_lock" in calls[0][0]
    assert calls[0][1] == {"lock_id": db._MIGRATION_LOCK_ID}
    assert calls[1] == ("commit", {})
    assert "pg_advisory_unlock" in calls[2][0]
    assert calls[3] == ("commit", {})
    assert calls[-1] == ("dispose", {})
