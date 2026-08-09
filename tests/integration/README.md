# Integration Tests

These tests require a real Postgres and Redis, unlike `tests/unit` (fakes
only). Run:

```bash
docker compose -f docker-compose.dev.yml up -d
python -m alembic upgrade head
python -m pytest -m integration
```

They read connection settings from `.env` (copy `.env.example` if you don't
have one) via the same `Settings` class the application uses -- there is no
separate test-only DSN. `tests/integration/conftest.py` truncates every table
after each test for isolation; it does not spin up or tear down the
containers themselves (that's what `docker-compose.dev.yml` is for).
