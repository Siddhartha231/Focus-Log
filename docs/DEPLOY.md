# Putting the sync server online

The server is one Docker image plus a Postgres database. Everything below can
be paid for with the [GitHub Student Developer Pack](https://education.github.com/pack).

## Try it locally first

```bash
docker compose up --build
focuslog register --server http://localhost:8000
focuslog sync
```

Without Docker: `pip install -e ".[server]"` then `focuslog-server` (uses a
local SQLite file unless `DATABASE_URL` is set).

## Heroku (student pack: $13 a month of credit for 24 months)

```bash
heroku create my-focuslog
heroku stack:set container                      # build from the Dockerfile (heroku.yml)
heroku addons:create heroku-postgresql:essential-0
git push heroku main
heroku open /healthz                            # should say {"status": "ok", ...}
```

Heroku sets `DATABASE_URL` and `PORT` by itself, and the server reads both.
Heroku's URL starts with `postgres://`; `settings.py` rewrites it to the form
SQLAlchemy needs.

Once your own account exists, close sign-ups:

```bash
heroku config:set FOCUSLOG_ALLOW_REGISTRATION=0
```

Azure (student pack: $100 credit) also works: App Service can run the same
Docker image, with Azure Database for PostgreSQL next to it.

## Settings

| Variable | What it does |
| --- | --- |
| `DATABASE_URL` | Postgres URL. Defaults to a local SQLite file. |
| `PORT`, `HOST` | Where to listen. Defaults to `0.0.0.0:8000`. |
| `FOCUSLOG_ALLOW_REGISTRATION` | Set to `0` to stop new sign-ups. |
| `SENTRY_DSN` | Turns on Sentry error tracking if set. |

## Other student pack pieces that fit

- **A domain** (Name.com, Namecheap or .TECH, free for a year) so the server is
  `api.yourname.dev` instead of a Heroku address. Add it with
  `heroku domains:add` and point a CNAME at the target Heroku gives you.
- **Sentry**: create a Python project, copy its DSN, then
  `heroku config:set SENTRY_DSN=...`. Server errors show up there with a stack trace.
- **Codecov**: CI already uploads coverage. Add the repo on codecov.io and save
  the token as the `CODECOV_TOKEN` secret in the GitHub repo settings.
- **Codespaces**: the repo has a `.devcontainer`, so "Code > Create codespace"
  gives a ready environment with everything installed.
- **Doppler** if you'd rather not keep secrets in Heroku config by hand.

Always use `https://` for a real deployment. The login token is sent on every
request, and plain `http://` would expose it. (`focuslog login` assumes https
when you leave the scheme off.)
