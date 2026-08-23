"""python -m studio  → FastAPI on 127.0.0.1:8787"""

from __future__ import annotations

import os

from studio.python_env import reexec_in_venv_if_needed


def main() -> None:
    reexec_in_venv_if_needed()
    import uvicorn

    host = os.environ.get("STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("STUDIO_PORT", "8787"))
    uvicorn.run("studio.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
