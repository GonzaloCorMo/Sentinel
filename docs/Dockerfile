FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /docs

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY mkdocs.yml ./mkdocs.yml
COPY pages ./pages

EXPOSE 3001

CMD ["mkdocs", "serve", "-f", "mkdocs.yml", "-a", "0.0.0.0:3001"]
