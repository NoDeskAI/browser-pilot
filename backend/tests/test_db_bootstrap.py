import asyncio

from app import db


class FakePool:
    def __init__(self):
        self.closed = False
        self.rows = {}
        self.executed = []

    async def close(self):
        self.closed = True

    async def fetchrow(self, _query, key):
        return self.rows.get(key)

    async def execute(self, query, *args):
        self.executed.append((query, args))


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


def test_upgrade_skips_alembic_when_revision_is_current(monkeypatch, caplog):
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

    caplog.set_level("INFO", logger="db")
    info = db._upgrade_with_connection(object())

    assert info.current_revision_after == "0009"
    assert "Database schema is current (revision=0009)" in caplog.text


def test_upgrade_logs_pending_migration_context(monkeypatch, caplog):
    reset_db_state()
    monkeypatch.setattr(
        db,
        "_collect_migration_info",
        lambda _connection: db.MigrationInfo(
            current_revision="0008",
            target_revision="0010",
            pending_revisions=["0009", "0010"],
        ),
    )

    def fake_upgrade(*_args, **_kwargs):
        return None

    class FakeMigrationContext:
        def get_current_revision(self):
            return "0010"

    monkeypatch.setattr(db.command, "upgrade", fake_upgrade)
    monkeypatch.setattr(db.MigrationContext, "configure", lambda _connection: FakeMigrationContext())

    caplog.set_level("INFO", logger="db")
    info = db._upgrade_with_connection(object())

    assert info.current_revision_after == "0010"
    assert "Database migration starting current=0008 target=0010 pending=['0009', '0010']" in caplog.text
    assert "Database migration completed from=0008 to=0010 applied=['0009', '0010']" in caplog.text


def test_upgrade_failure_preserves_revision_context(monkeypatch, caplog):
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
    caplog.set_level("INFO", logger="db")

    try:
        db._upgrade_with_connection(object())
    except db.MigrationExecutionError as exc:
        assert str(exc) == "boom"
        assert exc.info.current_revision == "0008"
        assert exc.info.target_revision == "0009"
        assert exc.info.pending_revisions == ["0009"]
        assert "Database migration failed current=0008 target=0009 pending=['0009']: boom" in caplog.text
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
    monkeypatch.setattr(db, "_ensure_default_storage_config", lambda: _noop_async())

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


def test_attempt_init_blocks_on_migration_failure(monkeypatch, caplog):
    reset_db_state()

    async def fake_run_migrations(_database_url):
        info = db.MigrationInfo(current_revision="0008", target_revision="0009", pending_revisions=["0009"])
        raise db.MigrationExecutionError("migration failed", info)

    monkeypatch.setattr(db, "_run_migrations", fake_run_migrations)

    caplog.set_level("ERROR", logger="db")
    result = asyncio.run(db._attempt_init("postgresql://user:pass@localhost/db", 1))
    state = db.get_bootstrap_state()

    assert result == "blocked"
    assert not db.is_ready()
    assert state["status"] == "migration_failed"
    assert state["pendingRevisions"] == ["0009"]
    assert state["error"] == "migration failed"
    assert "Database migration failed current=0008 target=0009 pending=['0009']: migration failed" in caplog.text


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


async def _noop_async():
    return None


def test_default_s3_storage_config_uses_bundled_s3_config(monkeypatch):
    monkeypatch.setattr(db.config, "BUNDLED_S3_STORAGE_BOOTSTRAP", True)
    monkeypatch.setattr(db.config, "BUNDLED_S3_ACCESS_KEY", "browserpilot")
    monkeypatch.setattr(db.config, "BUNDLED_S3_SECRET_KEY", "secret")
    monkeypatch.setattr(db.config, "BUNDLED_S3_BUCKET", "browser-pilot")
    monkeypatch.setattr(db.config, "BUNDLED_S3_ENDPOINT", "http://localhost:9000")

    config = db._default_s3_storage_config()

    assert config == {
        "storage": "s3",
        "s3Bucket": "browser-pilot",
        "s3Region": "us-east-1",
        "s3AccessKey": "browserpilot",
        "s3SecretKey": "secret",
        "s3Endpoint": "http://localhost:9000",
        "s3Presign": True,
        "s3PresignExpires": 3600,
    }


def test_default_s3_storage_config_skips_without_bootstrap(monkeypatch):
    monkeypatch.setattr(db.config, "BUNDLED_S3_STORAGE_BOOTSTRAP", False)

    assert db._default_s3_storage_config() is None


def test_ensure_default_storage_config_does_not_override_existing(monkeypatch):
    pool = FakePool()
    pool.rows["storage_config"] = {
        "value": {
            "storage": "s3",
            "s3Bucket": "external-bucket",
            "s3Region": "us-east-1",
            "s3AccessKey": "external-access",
            "s3SecretKey": "external-secret",
            "s3Endpoint": "https://s3.example.com",
        }
    }
    db._pool = pool
    monkeypatch.setattr(
        db,
        "_default_s3_storage_config",
        lambda: {"storage": "s3", "s3Bucket": "browser-pilot"},
    )

    asyncio.run(db._ensure_default_storage_config())

    assert pool.executed == []


def test_ensure_default_storage_config_preserves_builtin(monkeypatch):
    pool = FakePool()
    pool.rows["storage_config"] = {"value": {"storage": "builtin"}}
    db._pool = pool
    monkeypatch.setattr(
        db,
        "_default_s3_storage_config",
        lambda: {"storage": "s3", "s3Bucket": "browser-pilot"},
    )

    asyncio.run(db._ensure_default_storage_config())

    assert pool.executed == []


def test_ensure_default_storage_config_repairs_empty_s3_config(monkeypatch):
    pool = FakePool()
    pool.rows["storage_config"] = {
        "value": {
            "storage": "s3",
            "s3Bucket": "",
            "s3Region": "",
            "s3AccessKey": "",
            "s3SecretKey": "",
            "s3Endpoint": "",
        }
    }
    db._pool = pool
    monkeypatch.setattr(
        db,
        "_default_s3_storage_config",
        lambda: {"storage": "s3", "s3Bucket": "browser-pilot", "s3Endpoint": "http://localhost:9000"},
    )

    asyncio.run(db._ensure_default_storage_config())

    assert len(pool.executed) == 1
    assert pool.executed[0][1] == (
        "storage_config",
        '{"storage": "s3", "s3Bucket": "browser-pilot", "s3Endpoint": "http://localhost:9000"}',
    )


def test_ensure_default_storage_config_inserts_when_missing(monkeypatch):
    pool = FakePool()
    db._pool = pool
    monkeypatch.setattr(
        db,
        "_default_s3_storage_config",
        lambda: {"storage": "s3", "s3Bucket": "browser-pilot"},
    )

    asyncio.run(db._ensure_default_storage_config())

    assert len(pool.executed) == 1
    assert pool.executed[0][1] == (
        "storage_config",
        '{"storage": "s3", "s3Bucket": "browser-pilot"}',
    )
