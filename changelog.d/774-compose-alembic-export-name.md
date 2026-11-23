Fixed

* **A new migration runs without rebuilding the backend image.** `docker-compose.yml` mounts `backend/alembic/` and `alembic.ini` read-only into the backend container, as it already mounts `backend/app/`, so `alembic upgrade head` on start sees migrations that landed after the image was built. Before, a stack built earlier stayed on its old head and every route reading a new table failed (found when the routines tables were missing after 0062 and 0063 landed).
* **The export file is named after the company it holds.** The app named it from the company name it kept in memory, which is empty after a fresh login, so the file came out as `company-<id>-export-<day>.json`. It now takes the name from the export's own content.
