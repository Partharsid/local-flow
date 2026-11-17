FROM python:3.12-slim
# Standard library only: nothing to install.
RUN useradd --system --uid 10001 --no-create-home lf && mkdir /data && chown lf /data
WORKDIR /app
COPY ingest.py export_dataset.py ./
USER 10001
ENV LF_DATA_DIR=/data LF_PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python", "ingest.py"]
