# Image for the sync server only (the CLI runs on your own machine).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"

# don't run as root inside the container
RUN useradd --create-home app
USER app

ENV PORT=8000
EXPOSE 8000
CMD ["focuslog-server"]
