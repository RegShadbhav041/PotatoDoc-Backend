"""
PotatoDoc backend — serves M1/M2/M3 + Ensemble to the Expo app.
Contract (must not break mobile):
  GET  /ping   -> "Hello, I am alive" (text)
  GET  /models -> {models:[ids], modelNames:{id:label}, default:id}
  POST /predict?model_id=<id> (multipart file) -> success {class, confidence, probabilities, is_unknown:false, individual?}
                                                     unknown {class:"Unknown", confidence, is_unknown:true, entropy, probabilities:{}, message}
  POST /gradcam?model_id=<id> (multipart file) -> {overlay: data-uri, model:"small_cnn"} (SmallCNN only for every model_id)
Limits: JPEG/PNG/WebP, 10MB. Class strings EXACT: Early Blight | Late Blight | Healthy | Unknown
Model IDs: small_cnn (M1) | mobilenetv2 (M2) | efficientnetb0 (M3) | ensemble
  + legacy alias convnext_plantvillage -> efficientnetb0 (old mobile fallback still works).

Run:
  uvicorn app:app --host 0.0.0.0 --port 8000
  Phone + PC on same WiFi; set EXPO_PUBLIC_API_URL=http://<PC-LAN-IP>:8000 in mobile/.env
  Deployed: EXPO_PUBLIC_API_URL=https://<cloud-run-url> in mobile/.env

Notices + superadmin (additive; existing mobile contract above unchanged):
  GET    /notices[?category=<update|announcement|crop_alert|new_product|medicine>] public -> {items:[..., image_count], unread}
  GET    /notices/{id}/images/{index}  public -> image bytes (Cache-Control: public, max-age=86400)
  GET    /notices/unread-count  auth -> {unread}
  POST   /notices/{id}/read     auth -> {ok, unread}
  POST   /notices/read-all      auth -> {marked, unread}
  POST   /auth/me/photo  auth multipart file -> user {..., photo: data-uri}   (max 5MB -> 512px JPEG)
  DELETE /auth/me/photo  auth -> user {..., photo: null}
  /admin/*                      superadmin only -> 403 otherwise (admin.py)
  POST   /admin/notices/{id}/images         superadmin multipart file -> notice {..., image_count} (max 6)
  GET    /admin/notices/{id}/images/{index} superadmin -> image bytes (any status; panel preview)
  DELETE /admin/notices/{id}/images/{index} superadmin -> 204
  Support tickets (two-way farmer <-> superadmin chat):
  POST   /tickets                 auth -> 201 ticket {…, messages:[…]} (subject + first message)
  GET    /tickets                 auth -> {items:[…]} own tickets, newest activity first
  GET    /tickets/{id}            auth -> ticket + messages (owner only; 404 otherwise)
  POST   /tickets/{id}/messages   auth -> 201 ticket + messages (reopens a resolved ticket)
  GET    /admin/tickets[?status=<open|resolved>] superadmin -> all tickets + farmer identity
  GET    /admin/tickets/{id}      superadmin -> ticket + messages + farmer {…, photo}
  POST   /admin/tickets/{id}/messages superadmin -> 201 reply (status unchanged)
  PUT    /admin/tickets/{id}      superadmin -> {status: open|resolved}
  GET    /admin/users[/{id}]      now carry the farmer's profile photo as a data-URI
  Web panel: GET /admin/ (static/admin); account seeded from
  POTATO_SUPERADMIN_CONTACT + POTATO_SUPERADMIN_PASSWORD (see db.py).
"""
from pathlib import Path
import io, base64, math, os, json
import numpy as np
from PIL import Image

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import transforms, models
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db import init_db
from auth import router as auth_router
from history import router as history_router
from notices import router as notices_router
from tickets import router as tickets_router
from admin import router as admin_router
from location import router as location_router

# Repo root = this file's directory; override with POTATO_BASE_DIR if relocated.
BASE = Path(os.environ.get("POTATO_BASE_DIR", Path(__file__).resolve().parent))
# Switch weights dir without code change: POTATO_WEIGHTS_DIR=<dir> (restart server).
OUT = Path(os.environ.get("POTATO_WEIGHTS_DIR", str(BASE / "outputs_combined")))
CSV2API = {"early_blight": "Early Blight", "late_blight": "Late Blight",
           "healthy": "Healthy", "non_leaf": "Non-Leaf"}
def _load_classes():
    try:
        return json.loads((OUT / "labels.json").read_text())["api"]
    except Exception:
        return ["Early Blight", "Late Blight", "Healthy"]
CLASSES_API = _load_classes()
MEAN = [0.485, 0.456, 0.406]; STD = [0.229, 0.224, 0.225]
MAX_BYTES = 10 * 1024 * 1024

MODEL_NAMES = {
    "small_cnn": "Small CNN (from scratch)",
    "mobilenetv2": "MobileNetV2 (transfer)",
    "efficientnetb0": "EfficientNet-B0 (transfer)",
    "ensemble": "Ensemble (All Models)",
    "convnext_plantvillage": "EfficientNet-B0 (transfer)",
}
MEMBERS = ["small_cnn", "mobilenetv2", "efficientnetb0"]
ALIAS = {"convnext_plantvillage": "efficientnetb0"}

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
MODELS = {}

def build_arch(mid, n):
    if mid == "small_cnn":
        import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
        from small_cnn import SmallCNN
        return SmallCNN(n)
    if mid == "mobilenetv2":
        m = models.mobilenet_v2(); m.classifier[1] = nn.Linear(m.last_channel, n); return m
    if mid == "efficientnetb0":
        m = models.efficientnet_b0(); m.classifier[1] = nn.Linear(m.classifier[1].in_features, n); return m
    raise ValueError(mid)

def load_mid(mid):
    if mid in MODELS: return MODELS[mid]
    ckpt_p = OUT / mid / "best.pt"
    if not ckpt_p.exists():
        raise FileNotFoundError(f"weights missing: {ckpt_p} — train first (PV: train_image_pv.py, Irish: train_image.py)")
    ckpt = torch.load(ckpt_p, map_location=device, weights_only=False)
    n = int(ckpt.get("n_classes", len(CLASSES_API)))
    m = build_arch(mid, n).to(device); m.load_state_dict(ckpt["state"]); m.eval()
    csv_classes = ckpt.get("classes")
    api = [CSV2API.get(c, c) for c in csv_classes] if csv_classes else list(CLASSES_API)
    MODELS[mid] = (m, int(ckpt.get("img", 224)), bool(ckpt.get("transfer", mid != "small_cnn")), api)
    return MODELS[mid]

def tfm_for(img, transfer):
    norm = transforms.Normalize(MEAN, STD) if transfer else transforms.Normalize([0.5]*3, [0.5]*3)
    return transforms.Compose([transforms.Resize(int(img*1.14)), transforms.CenterCrop(img),
                               transforms.ToTensor(), norm])

def read_image(raw: bytes) -> Image.Image:
    if len(raw) > MAX_BYTES: raise HTTPException(413, "Image exceeds 10MB limit.")
    try: return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception: raise HTTPException(400, "Unreadable image. Send JPEG/PNG/WebP.")

@torch.no_grad()
def probs_of(mid, img: Image.Image):
    m, size, transfer, api = load_mid(mid)
    x = tfm_for(size, transfer)(img).unsqueeze(0).to(device)
    return torch.softmax(m(x), 1).cpu().numpy()[0], img, api

def green_ratio(img: Image.Image) -> float:
    """Fraction of leaf-green pixels. Potato leaves are green; persons/walls/soil mostly aren't.
    Cheap pre-gate before the CNN (no training needed). Calibrated: PV test leaves score 0.3–0.8."""
    a = np.asarray(img.resize((112, 112)).convert("RGB")).astype(int)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    return float(((G > R + 15) & (G > B + 15) & (G > 60)).mean())

def norm_entropy(p):
    p = np.clip(p, 1e-9, 1); h = float(-(p*np.log(p)).sum()); return h / math.log(len(p))

def _load_thresholds():
    import json as _j
    try:
        d = _j.loads((BASE / "calibration" / "thresholds.json").read_text())
        return float(d.get("entropy_max", 0.85)), float(d.get("prob_min", 0.55))
    except Exception:
        return 0.85, 0.55  # frozen heuristic until P0-4 val refit locks new values
ENT_MAX, PROB_MIN = _load_thresholds()

# ---------- Grad-CAM (manual, no extra dep) ----------
def last_conv(m):
    convs = [mo for mo in m.modules() if isinstance(mo, nn.Conv2d)]
    return convs[-1] if convs else None

def gradcam_overlay(mid, img: Image.Image) -> str:
    m, size, transfer, _api = load_mid(mid)
    x = tfm_for(size, transfer)(img).unsqueeze(0).to(device)
    layer = last_conv(m)
    store = {}
    def fwd(mo, i, o):
        o.retain_grad()
        store["a"] = o
    fh = layer.register_forward_hook(fwd)
    m.zero_grad(); out = m(x); cls = int(out.argmax(1)); out[0, cls].backward()
    fh.remove()
    A = store["a"][0].detach(); G = store["a"].grad
    if G is None:  # fallback: plain activation map
        cam = A.mean(0).relu().cpu().numpy()
    else:
        G = G[0].detach()
        w = G.mean(dim=(1, 2), keepdim=True)
        cam = (w * A).sum(0).relu().cpu().numpy()
    cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)
    cam = Image.fromarray((cam * 255).astype(np.uint8)).resize(img.size, Image.BILINEAR)
    import matplotlib.cm as cm
    heat = (np.array(cm.jet(np.array(cam) / 255.0))[:, :, :3] * 255).astype(np.uint8)
    base = np.array(img.resize((img.size[0], img.size[1])).convert("RGB"))
    blend = Image.fromarray(((0.45 * heat + 0.55 * base)).astype(np.uint8))
    buf = io.BytesIO(); blend.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

# ---------- app ----------
app = FastAPI(title="PotatoDoc backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Farmer accounts + synced history (see auth.py / history.py). Idempotent, so
# safe at import time. The ML endpoints above stay unauthenticated.
init_db()
app.include_router(auth_router)
app.include_router(history_router)
app.include_router(notices_router)
app.include_router(tickets_router)
app.include_router(admin_router)
app.include_router(location_router)

# Superadmin web panel — plain static HTML/JS, no build step. Guarded so the
# API still boots in a checkout that predates static/.
_ADMIN_DIR = BASE / "static" / "admin"
if _ADMIN_DIR.is_dir():
    app.mount("/admin", StaticFiles(directory=str(_ADMIN_DIR), html=True), name="admin")

@app.get("/ping", response_class=PlainTextResponse)
def ping(): return "Hello, I am alive"

@app.get("/models")
def get_models():
    return {"models": ["ensemble"] + MEMBERS,
            "modelNames": {k: MODEL_NAMES[k] for k in ["ensemble"] + MEMBERS},
            "default": "ensemble"}

def resolve(mid: str) -> str:
    mid = (mid or "ensemble").strip()
    return ALIAS.get(mid, mid)

@app.post("/predict")
async def predict(file: UploadFile = File(...), model_id: str = Query("ensemble")):
    mid = resolve(model_id)
    raw = await file.read()
    ctype = (file.content_type or "").lower()
    if not any(t in ctype for t in ("jpeg", "jpg", "png", "webp", "octet-stream")):
        raise HTTPException(400, "Send JPEG/PNG/WebP.")
    img = read_image(raw)
    # Green ratio is measured for transparency/debugging, but NEVER a hard reject on its own:
    # calibrated 2026-09-23 — fully necrotic early-blight leaves score green≈0.000, identical to
    # persons, so a color gate alone would discard real disease. Rejection is the trained
    # Non-Leaf class (gate 2) + entropy rule (gate 3), which use full appearance, not just color.
    g = green_ratio(img)
    try:
        if mid == "ensemble":
            ps, indiv, api = [], [], None
            for m in MEMBERS:
                p, _, api = probs_of(m, img); ps.append(p)
                indiv.append({"model": m, "class": api[int(p.argmax())], "confidence": float(p.max())})
            p = np.mean(ps, axis=0)
        elif mid in MEMBERS:
            p, _, api = probs_of(mid, img); indiv = None
        else:
            raise HTTPException(400, f"Unknown model_id '{model_id}'. Try /models.")
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    ent = norm_entropy(p); mx = float(p.max())
    pred_label = api[int(p.argmax())]
    # Gate 2: the trained 4th class. Non-Leaf -> mobile Unknown card (contract unchanged).
    if pred_label == "Non-Leaf":
        return {"class": "Unknown", "confidence": mx, "is_unknown": True, "entropy": ent,
                "probabilities": {}, "model_label": "Non-Leaf", "green_ratio": round(g, 3),
                "message": "This looks like a person, object, or non-potato image — not a potato leaf. Please retake with a potato leaf."}
    if ent > ENT_MAX or mx < PROB_MIN:  # Gate 3: val-frozen Unknown rule (calibration/thresholds.json)
        return {"class": "Unknown", "confidence": mx, "is_unknown": True, "entropy": ent,
                "probabilities": {},
                "message": "This does not look like a clear potato leaf. Retake: fill frame, good light, in-focus leaf."}
    out = {"class": pred_label, "confidence": mx, "is_unknown": False,
           "entropy": ent, "green_ratio": round(g, 3),
           "probabilities": {c: float(v) for c, v in zip(api, p)}}
    if mid == "ensemble": out["individual"] = indiv
    return JSONResponse(out)

@app.post("/gradcam")
async def gradcam(file: UploadFile = File(...), model_id: str = Query("ensemble")):
    mid = resolve(model_id)
    if mid != "ensemble" and mid not in MEMBERS:
        raise HTTPException(400, f"Unknown model_id '{model_id}'.")
    img = read_image(await file.read())
    try:
        # Product decision 2026-10-01: always return the SmallCNN heatmap only.
        # MobileNetV2/EfficientNetB0 overlays are no longer displayed; single
        # {overlay} shape is what the app renders for every model_id.
        return {"overlay": gradcam_overlay("small_cnn", img), "model": "small_cnn"}
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
