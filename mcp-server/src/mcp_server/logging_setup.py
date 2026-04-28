"""Optional Azure Monitor / Application Insights logging setup."""
from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn_str:
        logging.getLogger(__name__).info(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set; skipping Azure Monitor."
        )
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=conn_str,
            logger_name="mcp_server",
        )
        logging.getLogger(__name__).info("Azure Monitor exporter configured.")
    except Exception as exc:  # pragma: no cover - best-effort telemetry
        logging.getLogger(__name__).warning(
            "Failed to configure Azure Monitor: %s", exc
        )
