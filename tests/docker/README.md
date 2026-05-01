# Docker Test Harness

Full-stack tests that run against a real Home Assistant container. Exercises
the parts of this repo that unit-level tests cannot reach (install scripts,
deploy scripts, reload services, blueprint registration) by driving them
end-to-end against HA's REST API.

## When to use

- **Default pytest runs skip this suite.** The suite is slow (container build
  \+ boot + onboarding take roughly 30 seconds per session) and requires
  docker. Running via `./tests/run_all.py` or `pytest tests/` excludes it
  automatically.
- **Run it before you commit a change to install / deploy scripts**, or
  whenever you suspect a regression in the scripts or blueprint registration
  pipeline.
- **Use it interactively** when you want to poke HA's UI during development --
  see below.

## Running the tests

From the repo root:

```bash
pytest -m docker tests/docker
```

All docker tests are marked with `@pytest.mark.docker`. The pytest config in
`pyproject.toml` sets `addopts = -m 'not docker'` so `-m docker` is required
to opt in.

First run builds the custom image (~1 minute); subsequent runs reuse the layer
cache.

## Interactive development

To bring up the same environment the tests use and poke it in a browser:

```bash
HAPA_CONFIG_DIR=/tmp/hapa-cfg \
HAPA_TEST_DIR=/tmp/hapa-test \
docker compose -f tests/docker/docker-compose.yml up --build
```

HA is at <http://localhost:8123>. Onboard manually via the UI or run
`python3 tests/docker/_harness/ha_onboard.py --base-url http://127.0.0.1:8123`
to complete onboarding programmatically; the script prints an access token on
stdout.

SSH is available on port 2222 from the host if you bind-mount a test-only
authorized_keys into `$HAPA_TEST_DIR/authorized_keys` before starting.

## Layout

- `Dockerfile` -- extends the upstream HA image with `sshd`, `git`, and
  `bash`.
- `cont-init.d/` -- s6 one-shot scripts that run before services start (sshd
  host-key generation, authorized_keys seeding).
- `services.d/` -- s6 long-run service definitions (sshd alongside HA).
- `docker-compose.yml` -- single-service compose wired up by the pytest
  fixture.
- `_harness/ha_onboard.py` -- stdlib-only onboarding driver.
- `conftest.py` -- session fixtures (container lifecycle, API helpers,
  repo-into-container copy helper).
- `test_dev_workflow.py` -- the actual tests (dev-install.py, dev-deploy.py).

## Prerequisites

- Docker Desktop (macOS) or docker + docker compose (Linux). Tested against
  Docker Desktop 29+ on macOS.
- Internet access on first run to pull the HA image (~600MB).
