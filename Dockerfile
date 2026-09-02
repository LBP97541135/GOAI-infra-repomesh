FROM python:3.12-slim

# PyPI is unreliably slow from this team's network: cryptography's large index
# page regularly exceeds pip's resolver timeout, surfacing as "from versions:
# none" and failing the build. Default to a fast regional mirror; override per
# build with `docker compose build --build-arg PIP_INDEX_URL=...`.
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY capabilities ./capabilities

RUN pip install --no-cache-dir .

# Operator scripts travel with the image so a containerised console can be
# seeded in place: `docker compose --profile console exec console-api python
# scripts/seed-console-demo.py` picks the database up from the container's own
# REPOMESH_DATABASE_URL. Copied after the install so editing a script does not
# invalidate it.
COPY scripts ./scripts

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn repomesh.main:app --host 0.0.0.0 --port 8000"]
