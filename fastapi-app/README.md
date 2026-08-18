# fastapi-app

FastAPI boilerplate packaged to run as a Vercel Python serverless function.

```
fastapi-app/
├── api/index.py       # the FastAPI app (Vercel's entrypoint)
├── requirements.txt   # runtime deps Vercel installs
├── vercel.json        # rewrites all paths to the function
└── tests/test_api.py
```

## Endpoints

| Method | Path                 | Purpose                              |
|--------|----------------------|--------------------------------------|
| GET    | `/`                  | Service info                         |
| GET    | `/api/health`        | Liveness probe                       |
| POST   | `/api/echo`          | Validated request → typed response   |
| GET    | `/api/items/{id}`    | Path + query params, with error path |
| GET    | `/docs`              | Swagger UI (auto-generated)          |

## Run locally

```bash
cd fastapi-app
pip install -r requirements.txt uvicorn pytest httpx
uvicorn api.index:app --reload      # http://127.0.0.1:8000/docs
pytest tests -q
```

## Deploy to Vercel

Deployment must run from a machine with network access to `vercel.com` and
your Vercel credentials — it cannot be done from a sandboxed CI/agent
container whose egress policy blocks Vercel.

```bash
npm i -g vercel        # or use npx vercel@latest
vercel login           # opens a browser to authenticate

cd fastapi-app
vercel                 # first run: creates the project, deploys a preview
vercel --prod          # promote to production
```

On the first `vercel` run, accept the defaults except:

- **In which directory is your code located?** → `./` (you are already in
  `fastapi-app`)

If you instead link the whole repository to Vercel, set the project's
**Root Directory** to `fastapi-app` in Project Settings → General, otherwise
Vercel looks for an entrypoint at the repo root and fails with
`No FastAPI entrypoint found`.

### Environment variables

Set these under Project Settings → Environment Variables:

| Variable          | Default | Purpose                                        |
|-------------------|---------|------------------------------------------------|
| `APP_NAME`        | `fastapi-on-vercel` | Title shown in the app and docs    |
| `ALLOWED_ORIGINS` | `*`     | Comma-separated CORS allowlist. **Tighten this before production** — the default permits any origin. |

## Serverless constraints worth knowing

Vercel functions are stateless and their filesystem is read-only except
`/tmp`, which is not shared between invocations. So do not store uploads,
session state, or a database on local disk — anything that must survive
between requests belongs in Vercel Blob, S3, or a hosted database. The
endpoints in this starter are all stateless by design.
