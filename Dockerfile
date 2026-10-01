FROM python:3.11-slim

RUN pip install --no-cache-dir torch torchvision \
      --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py small_cnn.py ./
COPY outputs_image/ ./outputs_image/
COPY calibration/ ./calibration/

ENV POTATO_WEIGHTS_DIR=/app/outputs_image
EXPOSE 8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
