"""Configure JSON logging for standard logging and structlog."""

import structlog


def _processors():
    """Return processors shared by standard and structured log records."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]


def configure_structlog():
    """Configure structlog to emit records through standard logging."""
    structlog.configure(
        processors=[
            *_processors(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def json_formatter():
    """Build the formatter used by the console handler."""
    return structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[*_processors(), structlog.processors.format_exc_info],
    )
