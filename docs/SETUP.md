# Litreel Setup

## Part 1: Access Production Website (For CS372 TA/Grader)

1. **Open the creation studio:** The live website is hosted at https://litreel-9f4b585284df.herokuapp.com/
2. **Sign up with your own account or use the QA (testing) account:** `testuser@litreel.app` / `TestDrive123!` allows for easy access to a testing account you can use without creating your own account.
Alternatively, you can create your own account using an email and password (feel free to make the password a placeholder like "123456"). 

### Try the Emotional Arousal Model (component of creation studio)
The fine-tuned MiniLM-L6 transformer that powers random slice ranking during RAG is deployed as a standalone Hugging Face Space at [https://huggingface.co/spaces/RohanJoshi28/narrative-arousal-regressor](https://huggingface.co/spaces/RohanJoshi28/narrative-arousal-regressor) that **you can test!** (might take a sec to boot) In the actual project, this model scores ~30 random book chunks and selects the most emotionally charged passages to send to Gemini for micro-lesson/viral reel generation. You can test it independently—paste any narrative text and see its predicted arousal score (Gaussian-distributed, centered around 0, where higher values indicate more emotionally intense passages).

**Example inputs to try for transformer model:**
- **Low arousal (expect negative score):** *"The sun rose slowly over the quiet lake. Birds chirped softly in the distance, and the water was still, reflecting the pale morning sky."* — This calm, descriptive passage should produce a negative normalized value.
- **High arousal (expect positive score):** *"Smoke and fire roared around them, the building collapsing with a deafening crash. She reached out, screaming for help, her breath ragged and wild."* — This intense, chaotic passage should produce a positive value.

---

## Part 2: Local Development Setup

### Prerequisites
- Python 3.11 (matches `runtime.txt`) with `pip`
- `ffmpeg` available on your `PATH` for PyAV rendering
- SQLite (bundled with Python) for local persistence
- Recommended API access: Google Gemini key, Pexels API key, Supabase project + service role key

## 1. Create a Local Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# or `make install`
```

## 2. Configure Environment Variables
1. Copy the committed `.env` file (or create a new one) to manage your local secrets.
2. Provide the minimum secrets:
   ```dotenv
   GEMINI_API_KEY=your_google_genai_key
   GEMINI_MODEL_NAME=gemini-2.5-pro
   PEXELS_API_KEY=optional_but_nice
   LOG_VERBOSITY=essential
   ```
3. For Supabase-backed workflows also set:
   ```dotenv
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_API_KEY=service_role_key
   DATABASE_URL=postgresql://.../postgres?sslmode=require
   ```

## 3. Run the App Locally
```bash
export FLASK_APP=app.py
flask run --debug
# or `make start`
```
The server will initialize the SQLite database, seed the legacy QA account, and serve `/` plus the `/studio` editor.

## Supabase Configuration
1. **Provision the schema:** Paste `supabase_schema.sql` into the Supabase SQL editor. This creates `users`, `projects`, `concepts`, `slides`, `slide_styles`, `render_artifacts`, and `app_logs`.
2. **Point SQLAlchemy at Supabase:** Export the `postgresql://` string (include `?sslmode=require` behind pgbouncer) as `DATABASE_URL` in Heroku/Render.
3. **Disable auto migrations:** Set `AUTO_DB_BOOTSTRAP=0` once Supabase owns the schema so Flask skips `db.create_all()` in production builds.
4. **Retain the service-role key:** `SUPABASE_URL` + `SUPABASE_API_KEY` powers both Supabase RAG and the Supabase log handler. Leave `SUPABASE_LOG_TABLE=app_logs` to persist warning/error rows.
5. **Verify logging:** Trigger a warning or error endpoint and confirm a new entry appears in `app_logs` with request metadata (`request_id`, `status_code`, `duration_ms`).

## Local vs Supabase Storage
- `DATABASE_PROFILE=local` (default) keeps everything inside SQLite, including the mirrored `book` + `book_chunk` tables used by the local RAG path.
- Unset `DATABASE_PROFILE` and provide `DATABASE_URL`, `SUPABASE_URL`, and `SUPABASE_API_KEY` to route persistence and RAG traffic through Supabase.

## Background Queue & Worker
- `/api/projects` enqueues `generate_project_job` so uploads acknowledge immediately and avoid Heroku’s 30-second limit. Projects stay `pending/processing` until the worker marks them `generated`.
- Rendering and downloads run asynchronously: `POST /api/projects/<id>/downloads` enqueues `process_render_job`, uploading MP4s to `RENDER_STORAGE_BUCKET` in Supabase Storage. The studio polls `GET /api/downloads/<job_id>` until the signed URL is ready.
- Concept Lab refreshes also ride the queue via `process_concept_lab_job` so Gemini runs don’t block the UI. Local/dev profiles skip Redis and execute inline.
- Configure `REDIS_URL`, `WORK_QUEUE_NAME`, and `WORK_QUEUE_TIMEOUT`, then run `python worker.py` (or scale a Heroku worker dyno) alongside the web process.
- When Redis is unavailable, set `ENABLE_SYNC_DOWNLOAD=1` to fall back to synchronous downloads for debugging.

## Helpful Project Commands
- `make install` – bootstrap virtualenv + dependencies
- `make start` – run Flask with `--debug`
- `make test` – execute the pytest suite

## Running Tests
```bash
source .venv/bin/activate
pytest
# or `make test`
```
The backend suite covers document parsing, Gemini prompting, Supabase RAG flows, API serialization, renderer orchestration, and worker plumbing.

## Environment Variables

### Required API Keys
| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required for Gemini content generation and embedding calls. |
| `GEMINI_MODEL_NAME` | Gemini model to use (e.g., `gemini-2.5-pro`). |
| `LEMONFOX_API_KEY` | Required for text-to-speech narration via the Lemonfox TTS API. |
| `PEXELS_API_KEY` | Required for stock image search when users pick slide backgrounds. |

### Production Infrastructure
| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Supabase project URL (e.g., `https://<project>.supabase.co`). |
| `SUPABASE_API_KEY` | Supabase service role key. |
| `DATABASE_URL` | Postgres connection string (e.g., `postgresql://...?sslmode=require`). |
| `REDIS_URL` | Redis connection string for background job queue. |
| `AUTO_DB_BOOTSTRAP` | Set to `0` in production so Flask skips local migrations. |
| `RENDER_STORAGE_BUCKET` | Supabase Storage bucket for rendered MP4s (e.g., `litreel-renders`). |
| `SECRET_KEY` | Flask secret key for session signing. |

### Local Development
| Variable | Purpose |
| --- | --- |
| `DATABASE_PROFILE` | Set to `local` to use SQLite instead of Postgres. |
| `LOG_VERBOSITY` | Controls log output: `none`, `essential`, or `verbose`. |

### Testing
| Variable | Purpose |
| --- | --- |
| `LEGACY_USER_EMAIL` | Email for the seeded QA account (default: `testuser@litreel.app`). |
| `LEGACY_USER_PASSWORD` | Password for the seeded QA account (default: `TestDrive123!`). |

### Optional
| Variable | Purpose |
| --- | --- |
| `AROUSAL_SPACE_URL` | Hugging Face Space URL for the Narrative Arousal model. Only needed for random slice mode with emotion ranking. |
