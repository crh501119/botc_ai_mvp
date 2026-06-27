# BOTC AI MVP

Local unofficial fan project for a six-player No Greater Joy Teensyville-style game:
1 to 6 human players, isolated AI players filling the remaining seats, a deterministic
Python rules engine, and a React UI.

The app supports Mock AI offline play, OpenAI-backed AI players, share-link multiplayer
rooms, save/resume through SQLite, postgame reveal, transcript export, and estimated API
usage tracking. See [README.zh-TW.md](README.zh-TW.md) for full setup instructions and
[docs/deployment-public.md](docs/deployment-public.md) for public URL deployment notes.

## Quick Start

```powershell
copy .env.example .env
.\run.ps1
```

or:

```bash
cp .env.example .env
./run.sh
```

Docker:

```bash
cp .env.example .env
docker compose up --build
```

Open the shown local URL. `MOCK_AI=true` works without an OpenAI API key.

## Tests

```bash
python -m pytest
cd frontend
pnpm run test
```

This is not affiliated with The Pandemonium Institute and does not include official art,
icons, logos, fonts, PDFs, or role text.
