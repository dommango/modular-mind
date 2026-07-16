# modular-mind-web

The web front end for the modular-mind corpus — a Next.js app that surfaces the
generated VCV Rack patches and their rendered audio (the "listen" experience) from
the corpus pipeline. Static patch/audio data is committed under `public/data` and
`public/audio` and refreshed from the pipeline outputs.

## Local development

```bash
cd web
npm install
npm run dev        # http://localhost:3000
```

## Refreshing the data

Regenerate the committed `public/data` + `public/audio` payload from the pipeline
outputs (run from the repo root, using the project venv):

```bash
.venv/bin/python export_frontend_data.py
```

## Deploy

Hosted on Railway (project `modular-mind-render`, service `modular-mind-web`). The
service's **Root Directory** must be set to `web` in Railway so `web/railway.json`
and `web/Dockerfile` are used; then:

```bash
railway up --service modular-mind-web
```

Live URL: https://modular-mind-web-production.up.railway.app
