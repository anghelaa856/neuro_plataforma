"""Configuración SSL segura para llamadas HTTPS (OpenRouter, etc.)."""

from __future__ import annotations

import logging
from typing import Any, Union

import certifi

logger = logging.getLogger(__name__)

_configured = False
VerifyType = Union[bool, str]


def configure_secure_ssl() -> VerifyType:
    """
    Prepara verificación SSL.

    1) Intenta truststore (usa el almacén de certificados del SO / Windows).
    2) Si no está disponible, usa el CA bundle de certifi.

    En Windows con antivirus/proxy que intercepta HTTPS, certifi solo suele
    fallar; truststore.inject_into_ssl() + verify=True es lo que desbloquea.
    """
    global _configured
    ca_path = certifi.where()

    try:
        import truststore

        truststore.inject_into_ssl()
        if not _configured:
            logger.info("SSL: truststore inyectado (CA del sistema). certifi=%s", ca_path)
            _configured = True
        # Tras inject_into_ssl, verify=True usa el contexto del SO.
        return True
    except Exception as exc:
        if not _configured:
            logger.warning(
                "SSL: truststore no disponible (%s). Usando certifi.where()=%s",
                exc,
                ca_path,
            )
            _configured = True
        return ca_path


def ssl_verify() -> VerifyType:
    """Valor listo para pasar a requests (... verify=ssl_verify())."""
    return configure_secure_ssl()


def requests_kwargs() -> dict[str, Any]:
    """Kwargs comunes de seguridad para requests."""
    return {"verify": ssl_verify()}
