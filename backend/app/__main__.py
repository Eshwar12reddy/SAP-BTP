"""Run the API with ``python -m app [--reload]``, using APP_HOST / APP_PORT from settings.

On Windows, psycopg's async mode needs a SelectorEventLoop, but uvicorn defaults to the
Proactor loop when it runs without --reload. This launcher always picks a compatible loop.
"""

from __future__ import annotations

import sys

import uvicorn

from app.core.config import get_settings

SELECTOR_LOOP = "asyncio:SelectorEventLoop"


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload="--reload" in sys.argv[1:],
        loop=SELECTOR_LOOP if sys.platform == "win32" else "auto",
        log_config=None,  # the application configures structured logging itself
        access_log=False,  # replaced by the request middleware
    )


if __name__ == "__main__":
    main()
