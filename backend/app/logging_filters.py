"""Keep credentials out of HTTP client and local proxy access logs.

HTTPX logs full request URLs at INFO and httpcore can log request targets and
raw exception objects at DEBUG. Do not rely on the production log level for
credential protection. No settings or real environment files are read here.
"""

import logging
import re
from urllib.parse import unquote


class RedactHttpCredentials(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        decoded = unquote(message)
        # Low-level transport logs may contain bytes, escaped targets, or nested
        # exceptions. Suppress their payload instead of attempting partial regex
        # redaction. This deliberately trades DEBUG detail for credential safety.
        if record.name.startswith("httpcore"):
            message = "HTTP transport event [details redacted]"
        elif record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, version, status = record.args
            if "?" in str(target):
                target = str(target).split("?", 1)[0] + "?[REDACTED]"
            record.args = (address, method, target, version, status)
            # Uvicorn's AccessFormatter unpacks these five arguments itself.
            # Keep its structured logging contract rather than flattening msg.
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return True
        elif "amap.com" in decoded.lower() or "_AMapService" in decoded:
            # Also handles percent-encoded parameter names and duplicate keys.
            message = re.sub(r"\?[^\s\"'<>]+", "?[REDACTED]", message)
        else:
            message = re.sub(r"(?i)([?&](?:key|jscode)=)[^&\s\"'<>]+", r"\1[REDACTED]", message)
        record.msg = message
        record.args = ()
        # These HTTP loggers do not need raw exception tracebacks; a traceback
        # can repeat the original full URL even when the message is redacted.
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def install_http_log_redaction() -> None:
    # Filters on parent loggers do not run for records emitted by descendants.
    names = {
        "httpx", "uvicorn.access", "httpcore", "httpcore.connection",
        "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks",
    }
    names.update(name for name in logging.Logger.manager.loggerDict if name.startswith("httpcore."))
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(item, RedactHttpCredentials) for item in logger.filters):
            logger.addFilter(RedactHttpCredentials())
