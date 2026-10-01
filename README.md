# PotatoDoc Backend

Standalone FastAPI service that serves the PotatoDoc mobile app's plant-disease
models (Small CNN, MobileNetV2, EfficientNet-B0 + ensemble).

Split out of the main training repo ([RegShadbhav041/PotatoDoc](https://github.com/RegShadbhav041/PotatoDoc))
so it can be versioned and deployed independently.

## API contract (consumed by `mobile/`)

| Endpoint | Method | Response |
|---|---|---|
| `/ping` | GET | `Hello, I am alive` (text) |
| `/models` | GET | `{models, modelNames, default}` |
| `/predict?model_id=<id>` | POST (multipart `file`) | classification JSON (`class`, `confidence`, `probabilities`, gates → `Unknown`) |
| `/gradcam?model_id=<id>` | POST (multipart `file`) | `{overlay}` or `{heatmaps:{id:{overlay}}}` |

`model_id`: `ensemble` (default) | `small_cnn` | `mobilenetv2` | `efficientnetb0` | `convnext_plantvillage` (legacy alias).

## Layout

```
app.py                 FastAPI service (architecture identical to main repo's backend/app.py)
small_cnn.py           SmallCNN class vendored from train_image_pv.py (no pandas/sklearn)
outputs_image/         3 x best.pt weights (~29 MB) + labels/config/metrics JSON
calibration/           thresholds.json (entropy/probability gates)
Dockerfile             CPU-only torch, listens on $PORT (8080 on Cloud Run)
```

## Run locally

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Env vars:
- `POTATO_BASE_DIR` — repo root (default: this file's directory)
- `POTATO_WEIGHTS_DIR` — weights dir (default: `<base>/outputs_image`)

## Deploy (Google Cloud Run, free tier)

```bash
gcloud run deploy potatodoc-backend --source . \
  --region us-central1 --allow-unauthenticated \
  --memory 2Gi --cpu 1 --min-instances 0
```

`--min-instances 0` is required to stay inside the always-free allowance
(scale-to-zero; cold start ~10–30 s).

Then set the app's `mobile/.env` in the main repo:

```
EXPO_PUBLIC_API_URL=https://<service-url>
```

## Updating weights

Copy the new `best.pt` files into `outputs_image/<model>/`, commit, push —
Cloud Run rebuilds automatically on your next deploy.
