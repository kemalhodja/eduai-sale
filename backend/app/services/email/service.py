import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.i18n import t

logger = logging.getLogger(__name__)


class EmailService:
    def _smtp_configured(self) -> bool:
        return bool(settings.smtp_host.strip())

    def _send_password_reset_sync(self, to_email: str, reset_url: str, locale: str) -> bool:
        subject = t("auth.password_reset_subject", locale)
        body = t("auth.password_reset_body", locale).replace("{url}", reset_url)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.smtp_from
        message["To"] = to_email
        message.set_content(body)

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
            return True
        except Exception:
            logger.exception("Failed to send password reset email to %s", to_email)
            return False

    async def send_password_reset(self, to_email: str, reset_url: str, locale: str = "tr") -> bool:
        if not self._smtp_configured():
            if settings.debug:
                logger.info("Password reset link for %s: %s", to_email, reset_url)
                return True
            logger.warning("SMTP not configured; password reset email not sent for %s", to_email)
            return False

        # smtplib bloklayan IO: event loop'u 20 sn'e kadar kilitler -> worker thread
        return await asyncio.to_thread(
            self._send_password_reset_sync, to_email, reset_url, locale
        )
