"""Выбор платёжного провайдера."""

from __future__ import annotations

import logging

from .base import Intent, PaymentError, PaymentProvider, Status
from .fake import FakeProvider
from .kaspi import STATE_BY_EVENT, KaspiProvider

logger = logging.getLogger("printhub.payments")

__all__ = [
    "FakeProvider", "Intent", "KaspiProvider", "PaymentError", "PaymentProvider",
    "STATE_BY_EVENT", "Status", "build_provider",
]


def build_provider(settings, credentials_loader) -> PaymentProvider:
    """Настоящий Kaspi — или заглушка, но только там, где она допустима.

    Заглушка принимает «оплату» нажатием кнопки. На боевом сервере это означало
    бы бесплатную печать для всех, поэтому она привязана к тому же флагу, что и
    вход без телеграма, а он с боевым окружением несовместим и роняет запуск.
    """
    if settings.dev_login:
        logger.warning("Платежи в режиме разработки: деньги не списываются")
        return FakeProvider()
    return KaspiProvider(settings.kaspi_url, credentials_loader)
