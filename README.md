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
| `/gradcam?model_id=<id>` | POST (multipart `file`) | `{overlay, model:"small_cnn"}` — SmallCNN heatmap only, for every `model_id` |

`model_id`: `ensemble` (default) | `small_cnn` | `mobilenetv2` | `efficientnetb0` | `convnext_plantvillage` (legacy alias).

### Auth + history (bearer token required)

`Authorization: Bearer <token>` — obtained from `/auth/register` or `/auth/login`.
**Open and unauthenticated** are `/ping`, `/models`, `/predict`, `/gradcam`.

| Endpoint | Method | Request | Response |
|---|---|---|---|
| `/auth/register` | POST | `{contact, name, password}` | **201** `{token, user:{id, name, contact, createdAt}}` |
| `/auth/login` | POST | `{contact, password}` | **200** `{token, user}` |
| `/auth/me` | GET | — | **200** `user` |
| `/auth/logout` | POST | — | **204**, always idempotent (revokes the token, safe to repeat) |
| `/history` | GET | — | **200** `{items:[…]}` newest first, max 50 |
| `/history` | PUT | `{items:[…]}` | **200** `{upserted}` — **upsert-only**, never deletes absent ids |
| `/history` | DELETE | — | **204** — the only path that clears the server copy |

Notes:
- `contact` is an unverified plain string (email **or** phone), not a login identity check.
- `imageUri` is stripped from every uploaded item — it is a device-local `file://` path.
- Errors always carry a plain-string `detail` (e.g. `{"detail":"Incorrect password"}`).
- Passwords are `hashlib.scrypt`; login is rate-limited to 10 failures per contact per 5 min.

## Layout

```
app.py                 FastAPI service (architecture identical to main repo's backend/app.py)
auth.py                register / login / require_user / me / logout (scrypt + opaque tokens)
db.py                  SQLite schema (users, sessions, history) + connect()
history.py             GET/PUT/DELETE /history, upsert-only, MAX_ITEMS = 50
small_cnn.py           SmallCNN class vendored from train_image_pv.py (no pandas/sklearn)
outputs_image/         3 x best.pt weights (~29 MB) + labels/config/metrics JSON
calibration/           thresholds.json (entropy/probability gates)
scripts/auth_smoke.sh  end-to-end curl check (register → me → history → logout)
test_*.py              unit suite (24 tests)
Dockerfile             CPU-only torch, listens on $PORT (8080 on Cloud Run)
start_backend.ps1      uvicorn + free Cloudflare quick tunnel (Windows)
```

## Run locally

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Env vars:
- `POTATO_BASE_DIR` — repo root (default: this file's directory)
- `POTATO_WEIGHTS_DIR` — weights dir (default: `<base>/outputs_image`)
- `POTATO_DB` — SQLite file (default: `potatodoc.db` next to `db.py`)
- `POTATO_TOKEN_TTL_DAYS` — session lifetime, default `30`

## Tests

```bash
python3 -m unittest discover -p "test_*.py"     # 24 unit tests, ~4 s
scripts/auth_smoke.sh http://127.0.0.1:8000     # end-to-end (server must be running)
```

The suite creates and deletes its own `potatodoc.db`; no extra pip packages
(`unittest` + stdlib only).

## Quick start with a public URL (free, no card) — current setup

```powershell
powershell -ExecutionPolicy Bypass -File start_backend.ps1
```

Starts uvicorn on `:8000` + a Cloudflare quick tunnel and prints a
`https://….trycloudflare.com` URL. Paste it into `..\PotatoDoc\mobile\.env`:

```
EXPO_PUBLIC_API_URL=https://<printed-url>
```

Requires `cloudflared` (`winget install cloudflare.cloudflared`). Constraints:
the PC must stay on/awake, and **the URL changes on every restart** (quick
tunnels are ephemeral — reprint the script output and update `.env`).

## Deploy (Google Cloud Run, free tier — needs a payment card on file)

GCP requires a billing account even for the always-free tier ($0 usage).
If/when a card is available:

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
