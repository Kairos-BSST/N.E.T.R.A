# N.E.T.R.A API Documentation

## Overview

N.E.T.R.A provides a REST API built with FastAPI for authentication, video ingestion, AI analysis, live monitoring, alerts, POI management, evidence access, reporting, Google Drive integration, and administration.

The Swagger UI provides the complete interactive API documentation, including:

* Available endpoints
* HTTP methods
* Request parameters
* Request and response schemas
* Authentication requirements
* Interactive endpoint testing

## API Modules

### Authentication

Handles user login, logout, sessions, and authenticated user information.

### Video Upload & Analysis

Provides video upload, analysis job management, analysis history, reports, evidence retrieval, and result access.

### Live Monitoring

Provides RTSP and webcam connection, live monitoring controls, frame retrieval, live streaming, and live analysis.

### Alerts

Provides alert configuration, recent alerts, alert rules, watchlists, external webhooks, and webhook testing.

### Person of Interest

Provides POI enrollment, retrieval, face-image management, and deletion.

### Google Drive

Provides Google authentication, Drive connection status, video file listing, and video retrieval.

### Evidence & Reports

Provides access to generated snapshots, annotated analysis videos, evidence clips, and PDF reports.

### Administration

Provides audit logs, analysis/scan management, user listing, and operator creation.

## Endpoint Groups

| Module          | Main Routes                                 |
| --------------- | ------------------------------------------- |
| Authentication  | `/login`, `/logout`, `/me`                  |
| Upload          | `/upload`                                   |
| Analysis        | `/analysis/jobs`, `/analysis/jobs/{job_id}` |
| History         | `/history`                                  |
| Reports         | `/analysis/jobs/{job_id}/report`            |
| Evidence        | `/evidence/...`                             |
| Live Monitoring | `/live/...`                                 |
| Alerts          | `/alerts/...`                               |
| POI             | `/poi/...`                                  |
| Google Drive    | `/auth/google/...`, `/drive/...`            |
| Administration  | `/admin/...`                                |
| Videos          | `/videos`                                   |

For the complete endpoint list, request schemas, response models, parameters, and authentication details, use the interactive Swagger documentation.

