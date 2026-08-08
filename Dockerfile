# ---------------------------------------------------------------------------
# One image, two jobs: it runs the bench emulator, and it runs the tests
# against it. Same image both sides, so the emulator and the drivers can never
# be built from different revisions of the protocol — which is exactly the
# failure a containerised bench is supposed to make impossible.
#
#   docker build -t hil-test-framework .
#   docker run --rm hil-test-framework                    # the test suite
#   docker run --rm -p 5025:5025 -p 50000:50000/udp \
#          hil-test-framework python -m hiltf.emulators   # the bench
#
# See docker-compose.yml for the interesting version, where the emulator is a
# service and the suites run against it over the container network.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, in their own layer: editing a test or a plan then rebuilds
# in seconds instead of re-resolving the dependency tree.
COPY pyproject.toml README.md LICENSE ./
COPY hiltf ./hiltf
RUN pip install -e ".[dev,visa]"

# Everything that changes often.
COPY config ./config
COPY tests ./tests
COPY examples ./examples

# The bench emulator's two listeners: SCPI over TCP, the DUT over UDP.
EXPOSE 5025/tcp 50000/udp

# NOTE ON THE USER: this image runs as root, deliberately. It is a test-runner
# and a throwaway emulator, not a network service, and it bind-mounts ./reports
# from the host — running as a fixed non-root uid is the standard way to make
# that mount unwritable on someone else's machine. If this image ever becomes
# something long-lived and exposed, that trade goes the other way.

CMD ["pytest", "-q"]
