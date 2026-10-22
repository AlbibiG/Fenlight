# Fen Light API changelog

What changed in the watch history API, newest first. To update an installed API, run its install command again.

## 2026-10-22

- [FEATURE] Named API keys, one per device, so one device can be locked out without changing the others. Any number of devices can still share one key.
- [FEATURE] Added the `fenlight-api-keys` command to view, add, show, reset, rename and remove the keys. Changes apply without restarting the API.
- [CODE] Installing over an older version moves its one key into the list as `default`. `--new-key` now replaces the key named `default`.
- [FEATURE] Added `POST /api/bookmarks/clear`, which removes every movie or episode resume point of a profile (Fen Light's "Clear Progress for All").
- [FEATURE] Added `POST /api/hidden/progress/drop-many`, which hides many shows from the progress lists at once.

## 2026-10-21

- [FEATURE] First version: a FastAPI service that keeps Fen Light's watch history (watched, in progress, play history, hidden shows and personal lists) in MariaDB behind an API key, with a self-extracting installer that runs it as a systemd service on Debian and Ubuntu.
- [FEATURE] Added `POST /api/episodes/info`, which returns the watched episodes and bookmarks of many shows in one request, for Fen Light's episode lists and widgets.
- [FEATURE] Added `GET /api/profiles`: the profiles that have history in a database, with counts.
- [FEATURE] Added `POST /api/import`, which adds history from another source and keeps the entries a profile already has.
- [FEATURE] `GET /api/watched/recently` takes `per_show=1` to return only each show's latest episode.
- [FEATURE] Added `GET /health` for the installer and uptime monitors.
- [FEATURE] Added the installer option `--new-key` to replace the API key.
- [FEATURE] Added the installer option `--reconfigure` to change the settings, and a `DB_PORT` setting for MariaDB on another port.
- [FEATURE] The installer can run unattended, with the settings given as environment variables and a generated key.
- [FEATURE] The installer tests the MariaDB login before saving the settings, asks again after a typo, and lists the databases the user can see.
- [CODE] Keeps MariaDB connections open between requests instead of logging in for every request.
- [CODE] Converts older tables to indexed columns after an update, so lookups no longer scan whole tables.
- [CODE] Returns long lists several times faster, and gzips larger answers.
- [CODE] A missing database answers 404 and a failed MariaDB login 503, with the reason written to the log.
- [CODE] The installer installs only the packages the API needs (no compiler), waits for other apt runs to finish, and shows apt's errors when a step fails.
- [CODE] The installer checks for Python 3.10 or newer and a running systemd before installing, and warns when the MariaDB user is `root`.
- [CODE] Building the installer on Windows works: it strips Windows line endings that would break it on Linux.
- [BUG] Fixed a duplicate watched row being added each time a movie was marked watched.
- [BUG] Fixed database passwords with a backslash or spaces being changed when the service read them.
- [BUG] A wrong API key answers 401 instead of 403, so Fen Light says to check the key. Keys are compared in constant time.
- [BUG] Fixed the service not starting while MariaDB was down.
