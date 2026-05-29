"""FastAPI web entrypoint.

Invoked as `python -m app.web` so we get a Python startup hook for the
observability layer (Sentry, structured logging) before handing control to
uvicorn. Passing `log_config=None` to uvicorn disables uvicorn's built-in
logging config, so uvicorn's `uvicorn`, `uvicorn.access`, and `uvicorn.error`
loggers fall through to the root logger we configured in init_logging() —
which means access logs are emitted as JSON in production.
"""

import logging
import os

import uvicorn

from app.core.observability import init_logging, init_sentry

logger = logging.getLogger(__name__)


def main() -> None:
    init_logging()
    init_sentry("web")
    # Railway (and most PaaS) route to an injected $PORT; default to 8000 for
    # local/docker parity where no PORT is set.
    port = int(os.environ.get("PORT", "8000"))
    logger.info("Web booting; uvicorn binding 0.0.0.0:%d", port)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
