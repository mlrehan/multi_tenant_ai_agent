"""Dev/test stand-in for the ``EmailSender`` port -- logs instead of sending.

A real provider (SES, SendGrid, Postmark, ...) is an ops integration wired up
in a later phase behind this same port; deliberately not built now since it
has no bearing on the authentication logic itself. The raw token is never
logged -- see docs/03-threat-model.md ("never log ... tokens").
"""

from __future__ import annotations

import logging

logger = logging.getLogger("iam_platform.email")


class ConsoleEmailSender:
    async def send_verification_email(self, *, to: str, token: str) -> None:
        logger.info("verification email queued", extra={"extra_fields": {"to": to}})

    async def send_password_reset_email(self, *, to: str, token: str) -> None:
        logger.info("password reset email queued", extra={"extra_fields": {"to": to}})


class ConsoleInvitationEmailSender:
    """Implements ``application.tenancy.ports.InvitationEmailSender`` -- same
    log-instead-of-send pattern as ``ConsoleEmailSender``."""

    async def send_invitation_email(self, *, to: str, token: str, tenant_name: str) -> None:
        logger.info(
            "tenant invitation email queued",
            extra={"extra_fields": {"to": to, "tenant_name": tenant_name}},
        )
