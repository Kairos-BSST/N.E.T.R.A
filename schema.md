# N.E.T.R.A Database Schema

## Overview

N.E.T.R.A uses SQLite for storing data. The database stores users, authentication sessions, analysis jobs, detected events, evidence, audit records, and persons of interest.

## Entity Relationship

```text
users
  │
  ├── sessions
  │
  ├── analysis_jobs
  │      │
  │      ├── analysis_events
  │      │
  │      └── evidence
  │
  ├── audit_logs
  │
  └── poi_persons
          │
          └── poi_faces
```

## users

Stores application users and their roles.

| Column          | Type        | Description                   |
| --------------- | ----------- | ----------------------------- |
| `id`            | INTEGER PK  | Unique user ID                |
| `username`      | TEXT UNIQUE | Login username                |
| `password_hash` | TEXT        | Hashed password               |
| `role`          | TEXT        | `administrator` or `operator` |
| `is_active`     | INTEGER     | Account status                |
| `created_at`    | TEXT        | Account creation timestamp    |

## sessions

Stores authenticated user sessions.

| Column       | Type        | Description                  |
| ------------ | ----------- | ---------------------------- |
| `id`         | INTEGER PK  | Session ID                   |
| `token_hash` | TEXT UNIQUE | Hashed session token         |
| `user_id`    | INTEGER FK  | Associated user              |
| `created_at` | TEXT        | Session creation timestamp   |
| `expires_at` | TEXT        | Session expiration timestamp |

Relationship:

`users.id → sessions.user_id`

## analysis_jobs

Stores video analysis tasks and their processing state.

| Column             | Type        | Description                     |
| ------------------ | ----------- | ------------------------------- |
| `id`               | INTEGER PK  | Internal database ID            |
| `job_id`           | TEXT UNIQUE | Public analysis job identifier  |
| `user_id`          | INTEGER FK  | User who initiated the analysis |
| `source`           | TEXT        | Video source                    |
| `stream_url`       | TEXT        | Stream URL where applicable     |
| `local_path`       | TEXT        | Local video path                |
| `original_name`    | TEXT        | Original video filename         |
| `status`           | TEXT        | Current analysis status         |
| `message`          | TEXT        | Status information              |
| `error`            | TEXT        | Error information               |
| `queued_at`        | TEXT        | Queue timestamp                 |
| `started_at`       | TEXT        | Processing start timestamp      |
| `completed_at`     | TEXT        | Completion timestamp            |
| `updated_at`       | TEXT        | Last update timestamp           |
| `progress`         | REAL        | Processing progress             |
| `frames_processed` | INTEGER     | Number of processed frames      |
| `total_frames`     | INTEGER     | Total source frames             |
| `result_json`      | TEXT        | Serialized analysis results     |
| `video_info_json`  | TEXT        | Serialized video metadata       |
| `extra_json`       | TEXT        | Additional job metadata         |
| `created_at`       | TEXT        | Record creation timestamp       |

Relationship:

`users.id → analysis_jobs.user_id`

## analysis_events

Stores events detected during video analysis.

| Column               | Type       | Description                            |
| -------------------- | ---------- | -------------------------------------- |
| `id`                 | INTEGER PK | Event database ID                      |
| `job_id`             | TEXT FK    | Associated analysis job                |
| `event_id`           | TEXT       | Event identifier                       |
| `event_type`         | TEXT       | Event category                         |
| `label`              | TEXT       | Event label                            |
| `plate_number`       | TEXT       | Detected plate number where applicable |
| `wall_clock_time`    | TEXT       | Event timestamp                        |
| `video_time_seconds` | REAL       | Event position in video                |
| `event_json`         | TEXT       | Serialized event information           |
| `created_at`         | TEXT       | Event creation timestamp               |

Relationship:

`analysis_jobs.job_id → analysis_events.job_id`

The combination of `job_id` and `event_id` is unique.

## evidence

Stores generated snapshots, clips, and other analysis evidence.

| Column          | Type        | Description                 |
| --------------- | ----------- | --------------------------- |
| `id`            | INTEGER PK  | Evidence database ID        |
| `evidence_id`   | TEXT UNIQUE | Evidence identifier         |
| `job_id`        | TEXT FK     | Associated analysis job     |
| `user_id`       | INTEGER FK  | Associated user             |
| `evidence_type` | TEXT        | Type of evidence            |
| `file_path`     | TEXT        | Stored evidence path        |
| `file_name`     | TEXT        | Evidence filename           |
| `sha256`        | TEXT        | SHA-256 integrity hash      |
| `created_at`    | TEXT        | Evidence creation timestamp |

Relationships:

`analysis_jobs.job_id → evidence.job_id`

`users.id → evidence.user_id`

## audit_logs

Stores security and operational audit records.

| Column          | Type       | Description                |
| --------------- | ---------- | -------------------------- |
| `id`            | INTEGER PK | Audit record ID            |
| `user_id`       | INTEGER FK | User performing the action |
| `action`        | TEXT       | Recorded action            |
| `job_id`        | TEXT       | Related analysis job       |
| `resource_type` | TEXT       | Resource category          |
| `resource_id`   | TEXT       | Resource identifier        |
| `details_json`  | TEXT       | Additional action metadata |
| `timestamp`     | TEXT       | Action timestamp           |

Relationship:

`users.id → audit_logs.user_id`

## poi_persons

Stores enrolled persons of interest.

| Column       | Type       | Description                 |
| ------------ | ---------- | --------------------------- |
| `id`         | TEXT PK    | POI identifier              |
| `name`       | TEXT       | POI name                    |
| `notes`      | TEXT       | Additional notes            |
| `enabled`    | INTEGER    | POI status                  |
| `created_by` | INTEGER FK | User who created the record |
| `created_at` | TEXT       | Creation timestamp          |
| `updated_at` | TEXT       | Last update timestamp       |

Relationship:

`users.id → poi_persons.created_by`

## poi_faces

Stores face data associated with persons of interest.

| Column         | Type    | Description            |
| -------------- | ------- | ---------------------- |
| `id`           | TEXT PK | Face record ID         |
| `poi_id`       | TEXT FK | Associated POI         |
| `file_path`    | TEXT    | Stored face image path |
| `file_name`    | TEXT    | Face image filename    |
| `embedding`    | BLOB    | Face embedding         |
| `detect_score` | REAL    | Face detection score   |
| `bbox_json`    | TEXT    | Face bounding-box data |
| `created_at`   | TEXT    | Creation timestamp     |

Relationship:

`poi_persons.id → poi_faces.poi_id`

POI face records are removed when their associated POI is deleted.

## Indexes

The database defines indexes for frequently accessed fields:

* `sessions.token_hash`
* `analysis_jobs.user_id`
* `analysis_jobs.queued_at`
* `analysis_events.job_id`
* `analysis_events.event_type`
* `evidence.job_id`
* `audit_logs.timestamp`
* `audit_logs.user_id`
* `poi_faces.poi_id`

## Relationships

```text
users
 ├── sessions
 ├── analysis_jobs
 │    ├── analysis_events
 │    └── evidence
 ├── audit_logs
 └── poi_persons
      └── poi_faces
```


Evidence additionally stores a SHA-256 hash to support integrity and traceability.
