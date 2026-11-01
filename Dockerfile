# PyBossa's upstream pins target this legacy Python generation.
FROM python:3.8-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PYBOSSA_SETTINGS=/app/docker/settings.py
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libldap2-dev libsasl2-dev libssl-dev libffi-dev \
    libjpeg-dev zlib1g-dev libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir pip==23.3.2 setuptools==57.5.0 wheel==0.41.3
COPY setup.py ./
COPY docker/constraints.txt docker/requirements.py docker/
RUN python docker/requirements.py \
    && python -m pip install --no-cache-dir --no-build-isolation \
       -c docker/constraints.txt -r /tmp/pybossa-requirements.txt gunicorn==20.1.0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN useradd --create-home --uid 1000 pybossa \
    && mkdir -p /app/uploads /app/pybossa/themes/default/static/css/gen \
       /app/pybossa/themes/default/static/js/gen \
    && chown -R pybossa:pybossa /app
USER pybossa
EXPOSE 5000
CMD ["gunicorn", "--bind=0.0.0.0:5000", "--workers=2", "--threads=2", "--timeout=120", "--access-logfile=-", "--error-logfile=-", "docker.wsgi:app"]
