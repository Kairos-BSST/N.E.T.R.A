# Setup (local)

## 1. Prerequisites

- Python 3.10+
- Recommended: NVIDIA GPU + matching CUDA torch for faster CSRNet/crowd; other detectors default to CPU
- Clone/pull the repo: https://github.com/Kairos-BSST/N.E.T.R.A.git
- Pull model weights under `models/` (weapon, OCR, anomaly, violence as used by the backend):

```bash
git lfs install
git lfs pull
```

## 2. Backend env

```bash
cd backend
copy .env.example .env
```

Fill in login + OAuth + webhook values in `.env` (do not commit `.env`).

## 3. Google Drive (optional but needed for Drive)

- Place OAuth client JSON at: `cloud_integration/google_oauth_client_secret.json`
- Keep this path in `.env`:

```text
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=../cloud_integration/google_oauth_client_secret.json
```

- Redirect URI must match Google Cloud exactly:

```text
http://127.0.0.1:8000/auth/google/callback
```

- If the OAuth app is in **Testing**, add each teammate’s Google email as a **Test user**

## 4. Install + run

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
```

## 5. Open app

- Go to: http://127.0.0.1:8000
- Frontend is served by the backend (no separate frontend server)
- Log in with the accounts set in your `.env`

## 6. Common issues

Drive fails for others usually because of this:

- They have `backend/.env` filled
- They have the OAuth JSON file (or their own client JSON)
- Their Gmail is added as a Google Test user
- Server is on port `8000` with the same redirect URI

## 7. Useful commands

```bash
# API docs
# http://127.0.0.1:8000/docs

# Recreate env from example
cd backend
Copy-Item .env.example .env
```
