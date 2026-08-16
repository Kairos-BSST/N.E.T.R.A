# Setup Instructions

## 1. Prerequisites

- Python 3.10+
- Recommended: NVIDIA GPU 
- Clone/pull the repo:

```bash
git clone https://github.com/Kairos-BSST/N.E.T.R.A.git
```

- Pull model weights (weapon, OCR, anomaly, violence, face):

```bash
git lfs install
git lfs pull
```

## 2. Backend env

```bash
cd backend
copy .env.example .env
```

Fill in login + OAuth + webhook (optional) values in `.env`.

## 3. Google Drive OAuth Setup

Google Drive integration is **optional** and is only required if you want to fetch videos from Google Drive.

The application uses **Google OAuth 2.0** to authenticate users and access their Google Drive files.

### Step 1: Create a Google Cloud Project

1. Open the **Google Cloud Console**.
2. Create a new project or select an existing project.
3. Enable the **Google Drive API** for the project.

### Step 2: Configure Google OAuth

1. Open **Google Auth Platform** in Google Cloud Console.
2. Configure the OAuth consent screen.
3. Configure the required application details.
4. Add the Google Drive scopes required by the application.
5. Create an **OAuth 2.0 Client ID**.
6. Select **Web Application** as the application type.

Add the following redirect URI:
http://127.0.0.1:8000/auth/google/callback

### Step 3: Configure Test User

If the OAuth application is in **Testing** mode, the Google account used to test the application must be added as a **Test User**.

1. Go to **Google Auth Platform → Audience**.
2. Locate the **Test users** section.
3. Click **Add users**.
4. Enter the Google/Gmail account that will be used to test the application.
5. Save the changes.

The user must sign in using the same Google account that has been added as a Test User.

### Step 4: Download OAuth Client JSON

After creating the OAuth Client ID:

1. Download the OAuth client JSON file from Google Cloud.
2. Rename it to:

```text
google_oauth_client_secret.json
```
3. Place it at:

```text
cloud_integration/google_oauth_client_secret.json
```

Expected project structure:

```text
N.E.T.R.A/
├── backend/
│   ├── .env
│   └── ...
├── cloud_integration/
│   └── google_oauth_client_secret.json
├── models/
└── ...
```

### Step 5: Configure `.env`

In `backend/.env`, add the following:

```env
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=../cloud_integration/google_oauth_client_secret.json
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
```

> **Important:** The `GOOGLE_OAUTH_REDIRECT_URI` must exactly match the redirect URI configured in Google Cloud.

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

Drive fails usually because of this:

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
