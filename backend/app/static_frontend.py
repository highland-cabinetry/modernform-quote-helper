from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class FreshFrontend(StaticFiles):
    """Never let a cached app shell point at removed Vite asset hashes."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        if Path(full_path).suffix.lower() == ".html":
            return FileResponse(full_path, stat_result=stat_result, status_code=status_code,
                                headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                                         "Pragma": "no-cache", "Expires": "0"})
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
