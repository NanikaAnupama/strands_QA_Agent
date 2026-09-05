# Lambda container image for the QA agent.
#
# Debian rather than the Amazon Linux Lambda base image: the run needs both
# Tesseract and the full Chromium shared-library set, and both are one apt
# install away on Debian but a source build on AL2023. The AWS Lambda Runtime
# Interface Client turns any image into a valid Lambda runtime, so the base
# distro is a free choice.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Baked into the image, NOT under $HOME — the handler repoints HOME at /tmp
    # so anything resolved from the home directory at runtime would be lost.
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
      libglib2.0-0 \
      ca-certificates \
      fonts-liberation \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/task

# awslambdaric is the Lambda runtime shim; boto3 is used by the S3/job store.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir awslambdaric boto3

# Installs Chromium *and* its apt dependencies in one step, which keeps this
# list in sync with whatever Playwright version requirements.txt pins.
RUN playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY src/ /var/task/

# Chromium flags, verified against a live Lambda invocation. Without the full
# set the browser process dies during new_page() with a driver assertion error:
#   --no-sandbox / --disable-setuid-sandbox  its sandbox cannot nest in Lambda's
#   --disable-dev-shm-usage                  /dev/shm is only 64 MB here
#   --no-zygote / --single-process           Lambda's PID namespace does not let
#                                            Chromium fork its usual helpers
# Override QA_CHROMIUM_ARGS on the function to retune without a rebuild.
ENV QA_USE_PIPELINE=1 \
    QA_EXTRACTION_DIR=/tmp/extraction \
    QA_CHROMIUM_ARGS="--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage --disable-gpu --no-zygote --single-process" \
    TESSERACT_CMD=/usr/bin/tesseract \
    HOME=/tmp \
    XDG_CONFIG_HOME=/tmp/.config \
    XDG_CACHE_HOME=/tmp/.cache

ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["qa_agent.cloud.handler.api_handler"]
