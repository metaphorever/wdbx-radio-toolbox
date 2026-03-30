import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn

if __name__ == "__main__":
    import os
    dev = os.getenv("WDBX_DEV", "").lower() in ("1", "true", "yes")

    uvicorn.run(
        "web.main:app",
        host="0.0.0.0",
        port=8000,
        reload=dev,
        reload_dirs=[str(Path(__file__).parent)] if dev else None,
        reload_excludes=["*.db", "*.db-shm", "*.db-wal", "*.log", "*.mp3"] if dev else None,
    )
