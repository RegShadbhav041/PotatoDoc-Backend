FROM python:3.11-slim

RUN pip install --no-cache-dir torch torchvision \
      --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py small_cnn.py auth.py db.py history.py notices.py admin.py ./
COPY outputs_combined/ ./outputs_combined/
COPY calibration/ ./calibration/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY test_*.py ./

# Cloud Run's filesystem is read-only outside /tmp, and the image is rebuilt
# on every deploy — a database inside the image would be wiped each time.
ENV POTATO_WEIGHTS_DIR=/app/outputs_combined
ENV POTATO_DB=/tmp/potatodoc.db

EXPOSE 8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
