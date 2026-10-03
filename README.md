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
| `/auth/me/photo` | POST | multipart `file` (≤5 MB) | **200** `user` with `photo` (base64 data-URI, 512px JPEG) |
| `/auth/me/photo` | DELETE | — | **200** `user` with `photo: null` |
| `/auth/logout` | POST | — | **204**, always idempotent (revokes the token, safe to repeat) |
| `/history` | GET | — | **200** `{items:[…]}` newest first, max 50 |
| `/history` | PUT | `{items:[…]}` | **200** `{upserted}` — **upsert-only**, never deletes absent ids |
| `/history` | DELETE | — | **204** — the only path that clears the server copy |

### Support tickets (two-way chat, bearer token required)

| Endpoint | Method | Request | Response |
|---|---|---|---|
| `/tickets` | POST | `{subject, message}` | **201** `ticket` — opens a ticket, `message` is the first entry |
| `/tickets` | GET | — | **200** `{items:[…]}` own tickets, newest activity first |
| `/tickets/{id}` | GET | — | **200** `ticket` + `messages` (owner only; **404** otherwise) |
| `/tickets/{id}/messages` | POST | `{body}` | **201** `ticket` + `messages`; **reopens** a resolved ticket |
| `/admin/tickets` | GET | `?status=open\|resolved` | **200** `{items:[…]}` all tickets + farmer identity |
| `/admin/tickets/{id}` | GET | — | **200** thread + `farmer {id,name,contact,photo}` |
| `/admin/tickets/{id}/messages` | POST | `{body}` | **201** reply (status unchanged — resolve explicitly) |
| `/admin/tickets/{id}` | PUT | `{status}` | **200** `open` or `resolved` |

Ticket fields: `id, subject, status(open\|resolved), created_at, updated_at, message_count, last_body`;
messages: `{id, body, created_at, author, from: farmer\|admin}`. Subject ≤ 200 chars, body ≤ 4000.
Superadmin routes ride the same 401/403 guard as the rest of `/admin/*`.
`GET /admin/users[/{id}]` now also returns the farmer's `photo` as a data-URI (or `null`).

Notes:
- `contact` is an unverified plain string (email **or** phone), not a login identity check.
- `imageUri` is stripped from every uploaded item — it is a device-local `file://` path.
- Errors always carry a plain-string `detail` (e.g. `{"detail":"Incorrect password"}`).
- Passwords are `hashlib.scrypt`; login is rate-limited to 10 failures per contact per 5 min.

### Notices (feed + superadmin image galleries)

Categories: `update` | `announcement` | `crop_alert` | `new_product` | `medicine`.

| Endpoint | Method | Request | Response |
|---|---|---|---|
| `/notices` | GET | — | **200** `{items:[{..., image_count}], unread}` (public) |
| `/notices/{id}/images/{index}` | GET | — | **200** image bytes, `Cache-Control: public, max-age=86400` (public; 404 for drafts/missing) |
| `/admin/notices/{id}/images` | POST | multipart `file` | **201** notice with `image_count` (superadmin; max 6) |
| `/admin/notices/{id}/images/{index}` | GET | — | **200** image bytes, any status (superadmin; panel preview) |
| `/admin/notices/{id}/images/{index}` | DELETE | — | **204** (superadmin; index = public GET position) |

## Layout

```
app.py                 FastAPI service (architecture identical to main repo's backend/app.py)
auth.py                register / login / require_user / me / logout (scrypt + opaque tokens)
db.py                  SQLite schema (users, sessions, history) + connect()
history.py             GET/PUT/DELETE /history, upsert-only, MAX_ITEMS = 50
small_cnn.py           SmallCNN class vendored from train_image_pv.py (no pandas/sklearn)
outputs_combined/       3 x best.pt weights (~29 MB) + labels/config/metrics JSON
                        (combined PV + Irish training family: 100% PV test, 98.75% Irish test)
calibration/           thresholds.json (entropy/probability gates)
scripts/auth_smoke.sh  end-to-end curl check (register → me → history → logout)
tests/               unit suite (unittest + stdlib only; run: python -m unittest discover -s tests)
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
- `POTATO_WEIGHTS_DIR` — weights dir (default: `<base>/outputs_combined`)
- `POTATO_DB` — SQLite file (default: `potatodoc.db` next to `db.py`)
- `POTATO_TOKEN_TTL_DAYS` — session lifetime, default `30`

## Tests

```bash
python3 -m unittest discover -s tests       # full unit suite, ~13 s (run from repo root)
scripts/auth_smoke.sh http://127.0.0.1:8000     # end-to-end (server must be running)
```

The suite creates and deletes its own `potatodoc.db`; no extra pip packages
(`unittest` + stdlib only).

## Quick start with a public URL (free, no card) — current setup

```powershell
powershell -ExecutionPolicy Bypass -File start_backend.ps1
```

Starts uvicorn on `:8000`. Public URL is FIXED via named tunnel:

```
EXPO_PUBLIC_API_URL=https://potatodoc.shadbhavregmi.com.np
```

Requires `cloudflared` connector running (`PotatoDoc` tunnel, Published application
route `potatodoc.shadbhavregmi.com.np -> http://127.0.0.1:8000`). The PC must stay
on/awake. Fallback to ephemeral quick tunnel: `start_backend.ps1 -Quick`
(prints a `https://….trycloudflare.com` URL).

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

Copy the new `best.pt` files into `outputs_combined/<model>/`, commit, push —
Cloud Run rebuilds automatically on your next deploy.
