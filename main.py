from __future__ import annotations

import uvicorn

from botc_ai.api.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
