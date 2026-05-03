"""PyInstaller entry point for the AzulClaw backend."""

import asyncio
import logging
import sys

from azul_backend.azul_brain.main_launcher import LOGGER, main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("AzulClaw stopped by user.")
    except Exception as error:
        logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
        LOGGER.exception("Fatal error: %s", error)
        sys.exit(1)
