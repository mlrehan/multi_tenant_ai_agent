"""One-off dev helper: writes .env with \\n-escaped PEM keys on single lines.

Not part of the application -- just used once during Phase 5 setup to avoid
shell quoting hazards when generating a working local .env by hand.
"""

from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parent.parent

priv = (root / "jwt_private.pem").read_text().strip().replace("\n", "\\n")
pub = (root / "jwt_public.pem").read_text().strip().replace("\n", "\\n")

env = f"""ENVIRONMENT=development
SECRET_PROVIDER=env

DATABASE__HOST=localhost
DATABASE__PORT=55432
DATABASE__NAME=iam_platform
DATABASE__USER=app_tenant
DATABASE__PASSWORD=dev_only_password
DATABASE__PLATFORM_USER=app_platform
DATABASE__PLATFORM_PASSWORD=dev_only_password
DATABASE__MIGRATOR_USER=postgres
DATABASE__MIGRATOR_PASSWORD=dev_only_superuser_password

REDIS__URL=redis://localhost:56379/0

JWT__ISSUER=https://auth.iam-platform.local
JWT__AUDIENCE=iam-platform-api
JWT__PRIVATE_KEY_PEM={priv}
JWT__PUBLIC_KEY_PEM={pub}

OAUTH_GOOGLE__ENABLED=false
OAUTH_FACEBOOK__ENABLED=false

CORS_ALLOWED_ORIGINS=["http://localhost:3000"]
LOG_LEVEL=INFO
"""

(root / ".env").write_text(env)
print("wrote", root / ".env")
print("private key line has backslash-n literal:", "\\n" in priv and "\n" not in priv)
