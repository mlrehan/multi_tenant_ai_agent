# Configuration & Secrets

## Typed settings via Pydantic Settings

**Decision:** `pydantic-settings` (`BaseSettings`) for one root `Settings` object, with nested settings groups as plain `BaseModel` classes rather than nested `BaseSettings`. Loaded once at process startup (API or worker) and passed explicitly through the composition root — never re-read or re-parsed mid-process.

> **Implementation note (confirmed empirically during Phase 5):** a nested class that is itself a `BaseSettings` with its own `env_prefix` does **not** get auto-populated by the parent `Settings` object — constructing the root with the prefixed env vars set still raises `field required`. The nested groups must be plain `BaseModel`; the root's `env_nested_delimiter="__"` combined with the *field name* (uppercased) is what produces the prefix (e.g. field `database` → `DATABASE__HOST`), not a prefix declared on the nested class itself. The design below reflects the working pattern.

```python
# core/config.py
class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str = "iam_platform"
    user: str = "app_tenant"
    password: SecretStr                 # resolved via SecretProvider, see below
    pool_size: int = 10
    pool_max_overflow: int = 20

class RedisSettings(BaseModel):
    url: SecretStr = SecretStr("redis://localhost:6379/0")

class JwtSettings(BaseModel):
    issuer: str = "https://auth.example.invalid"
    audience: str = "iam-platform-api"
    private_key_pem: SecretStr          # RS256/EdDSA signing key
    public_key_pem: str
    access_token_ttl_seconds: int = 900  # 15 min, per 05-authentication-flows.md

class OAuthProviderSettings(BaseModel):
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    redirect_uri: str = ""
    enabled: bool = False

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="forbid")

    environment: Literal["development", "staging", "production"] = "development"
    database: DatabaseSettings
    redis: RedisSettings = RedisSettings()
    jwt: JwtSettings
    oauth_google: OAuthProviderSettings = OAuthProviderSettings()
    oauth_facebook: OAuthProviderSettings = OAuthProviderSettings()
    secret_provider: Literal["env", "aws_secrets_manager", "vault", "azure_key_vault", "gcp_secret_manager"] = "env"
    cors_allowed_origins: list[str] = []
    log_level: str = "INFO"
```

- `extra="forbid"` on the root model — protects programmatic construction (e.g. a test passing an unexpected kwarg) rather than arbitrary unrelated OS environment variables, since pydantic-settings only ever reads the env vars that correspond to a declared field or nested prefix in the first place; it was never going to see (or care about) `PATH` or other unrelated env vars.
- Every field is typed and either required (no default → missing value fails startup with a clear Pydantic validation error) or has an explicit, safe default. There is no "settings dict with `.get(key, fallback)` scattered through the codebase" pattern anywhere.
- `SecretStr` wraps every secret-bearing field so it can never be accidentally interpolated into a log line or `repr()`'d in a stack trace — it prints as `**********`.

## Environment variable catalog (initial set)

| Variable | Sensitive? | Notes |
|---|---|---|
| `ENVIRONMENT` | No | `development` \| `staging` \| `production` |
| `DATABASE__HOST`, `DATABASE__PORT`, `DATABASE__NAME`, `DATABASE__USER` | No | |
| `DATABASE__PASSWORD` | **Yes** | resolved via secret provider in staging/production |
| `REDIS__URL` | **Yes** | may embed an auth token |
| `JWT__ISSUER`, `JWT__AUDIENCE` | No | |
| `JWT__PRIVATE_KEY_PEM` | **Yes** | signing key — production must source from secret manager, never a checked-in file |
| `JWT__PUBLIC_KEY_PEM` | No | safe to distribute (used by any service that only verifies) |
| `OAUTH_GOOGLE__CLIENT_ID` | No | |
| `OAUTH_GOOGLE__CLIENT_SECRET` | **Yes** | |
| `OAUTH_FACEBOOK__CLIENT_ID` | No | |
| `OAUTH_FACEBOOK__CLIENT_SECRET` | **Yes** | |
| `KMS__KEY_ID` | No | reference only; the KMS itself holds the key material for `provider_credentials`/`oauth_accounts` token encryption |
| `SECRET_PROVIDER` | No | selects which `SecretProvider` implementation is active |
| `CORS_ALLOWED_ORIGINS` | No | explicit allow-list, never `*` outside local development |
| `LOG_LEVEL` | No | |

`.env.example` in the repo root lists every variable with a placeholder or safe default and a one-line comment — it is the single source of truth for "what does this service need to run," and CI checks that every field `Settings` requires has a corresponding entry in `.env.example` (a schema/example drift check), so the example file can't silently go stale.

## External secret manager abstraction

**Decision:** a `SecretProvider` port in `infrastructure/secrets/base.py`, with the concrete provider selected by `SECRET_PROVIDER` at startup — application code never talks to AWS/Vault/Azure/GCP APIs directly, only through this port.

```python
# infrastructure/secrets/base.py
class SecretProvider(Protocol):
    async def get_secret(self, key: str) -> str: ...
```

```python
# infrastructure/secrets/env_provider.py  — development default
class EnvSecretProvider:
    async def get_secret(self, key: str) -> str:
        value = os.environ.get(key)
        if value is None:
            raise SecretNotFoundError(key)
        return value
```

```python
# infrastructure/secrets/aws_secrets_manager.py  — production option
class AwsSecretsManagerProvider:
    def __init__(self, client: "SecretsManagerClient", cache_ttl_seconds: int = 300) -> None: ...
    async def get_secret(self, key: str) -> str:
        # fetches from AWS Secrets Manager, short in-memory TTL cache to avoid
        # a network round trip on every settings access
        ...
```

Equivalent thin adapters exist for `vault_provider.py` (HashiCorp Vault, KV v2 engine) and `azure_key_vault.py` / `gcp_secret_manager.py` — same three-method shape, so switching providers is a one-line `SECRET_PROVIDER` change with no application code touched. This directly satisfies the "typed environment-based configuration and support external secret managers" requirement without hard-coding any single vendor into the core config-loading path.

**Resolution order at startup:** `Settings` is first constructed from environment variables/`.env` as normal (fast, no I/O). Fields marked `SecretStr` whose value looks like a *reference* (e.g. prefixed `secret://`) are then resolved through the active `SecretProvider` before the settings object is considered "ready" — e.g. `DATABASE__PASSWORD=secret://prod/db/password` triggers an `AwsSecretsManagerProvider.get_secret("prod/db/password")` call during startup, while a plain value (used in development) is taken as-is. This keeps local development friction-free (`.env` with plain values) while production configuration never contains a real secret in an environment variable at all — only a pointer to one. > **Phase 9 correction.** The paragraph above described the intended design, but through Phase 8 **no resolution step existed at all** — `SecretProvider` and `EnvSecretProvider` were defined and never called, so `DATABASE__PASSWORD=secret://prod/db/password` would have been used as a literal password string. Since `Settings` also refuses to start when `environment=production` and `secret_provider=env`, the service could not have started correctly in production. Resolution is now implemented in [`infrastructure/secrets/resolver.py`](../src/iam_platform/infrastructure/secrets/resolver.py) and called from `build_container` (which is async for exactly this reason), with `AwsSecretsManagerProvider` as the first real backend. Selecting `vault`, `azure_key_vault`, or `gcp_secret_manager` raises `NotImplementedError` at startup rather than silently falling back to `env`.

## Per-environment strategy

| Environment | Config source | Secret source | Notes |
|---|---|---|---|
| `development` | `.env` file (git-ignored, seeded from `.env.example`) | `EnvSecretProvider` (plain values) | Debug logging allowed; CORS may allow `localhost` |
| `staging` | Environment variables injected by the deploy pipeline | `AwsSecretsManagerProvider` (or org's chosen provider) via `secret://` references | Mirrors production config shape exactly, different underlying values, so staging is a true rehearsal of production config resolution |
| `production` | Environment variables injected by the deploy pipeline | Same provider as staging, production secret store | `extra="forbid"` and full field validation mean a missing production secret reference fails the deploy at container startup, not at first request |

**"Do not expose development configuration in production"** (Phase 1 §19) is enforced two ways: (1) `Settings.environment` is itself a required, validated field with no default, so a deploy that forgets to set `ENVIRONMENT=production` fails startup rather than silently running with development assumptions; (2) a small set of environment-conditional guards (e.g., refusing to start if `environment == "production"` and `secret_provider == "env"`, since that would mean production secrets are sitting in plain environment variables) run once at the end of `Settings` construction.

## Config-affects-behavior — kept explicit, not scattered

Anywhere behavior legitimately differs by environment (verbose error bodies in `development`, stricter cookie `Secure`/`SameSite` flags in `staging`/`production`), the check is `if settings.environment == "production"`, made once at the composition root (`api/main.py`) when constructing middleware/handlers — not sprinkled as ad hoc `if settings.environment == ...` checks throughout route handlers or domain code, which would violate the layer rules in [20-dependency-rules.md](20-dependency-rules.md) (domain code must not know about `Settings` at all).
