# PyBossa on Docker Desktop

Run commands from the repository root. Docker Desktop must be running.

```bash
git submodule update --init --recursive
python3 docker/setup.py
docker compose up --build -d
docker compose ps
```

Open **http://localhost:8085**. Sign in with email **admin@localhost.invalid**
and the value of `PYBOSSA_ADMIN_PASSWORD` in the repository's `.env` file.
The account's username is `admin`. The setup generates unique secrets and saves
the file with mode 600. Rerunning it preserves existing secrets. The bootstrap
creates the administrator only when the database has no users; it does not reset
an existing account's password.

Media setup adds a private `Downloads/pybossa-media` bind mount, with configurable
locations for each project. See [MEDIA_STORAGE.md](../MEDIA_STORAGE.md).

The installation includes the web application, a background worker, the job
scheduler, PostgreSQL and Redis. The initialization container creates a fresh
schema, stamps its Alembic revision, adds categories, prepares the leaderboard
and registers schedules. The web, worker and scheduler start after initialization
succeeds. Redis uses the direct connection supported by PyBossa; a Redis Sentinel
cluster is unnecessary for this single-host installation.

The application is available only through your Mac's loopback interface.
PostgreSQL and Redis have no published host ports. New accounts can be registered
locally without email verification; outbound mail is suppressed. This is a fresh
local installation and does not copy data from the live MicroMappers server.

## Stop and start

```bash
docker compose stop
docker compose start
```

To recreate containers while preserving data:

```bash
docker compose down
docker compose up -d
```

PostgreSQL, Redis and uploaded files persist in Docker named volumes. Do not use
`docker compose down -v` unless you intend to erase this local installation.
Containers restart automatically while Docker Desktop is running. Enable Docker
Desktop's start-at-login setting yourself if you want it to start after a Mac
reboot.

## Logs and verification

```bash
docker compose logs --tail=100 web worker scheduler
docker compose logs init
docker compose exec web python -m docker.smoke
docker compose exec web python -m docker.media_smoke
```

The smoke check uses the real application, database, Redis and templates. It
signs in through the normal login form and verifies editing a completed
annotation with CSRF enabled, audit logging, result versioning and stale-edit
rejection. Its temporary project and contribution are removed afterward.

To rebuild after changing Python code:

```bash
docker compose up --build -d
```

To use another port, change both `PYBOSSA_PORT` and `PYBOSSA_SERVER_NAME` in `.env`
(for example `8086` and `localhost:8086`), then recreate the containers.

## Runtime compatibility

The upstream 2022 requirements depend on old Flask and SQLAlchemy APIs. This image
uses Python 3.8 on Debian Bookworm and constrains upstream's unbounded dependency
versions. It uses Linux amd64 wheels, with Docker Desktop emulation on Apple
Silicon. Package/build tools are pinned because several upstream dependencies
still use the removed `use_2to3` build option. These choices preserve upstream
compatibility; they are not a dependency modernization or an Internet-facing
production deployment configuration.

`Dockerfile`, `compose.yaml` and the `docker/` directory are reusable. `.env` is
excluded from Git and from the image build context. Keep a protected copy of it
alongside your database backups, because it contains the installation's secrets.
