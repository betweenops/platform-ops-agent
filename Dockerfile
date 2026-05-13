FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY fixtures /app/fixtures

RUN pip install --no-cache-dir .

ENTRYPOINT ["platform-ops-agent"]
