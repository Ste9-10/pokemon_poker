# Pokémon Poker - Flask

This repository contains a Flask-based multiplayer Pokémon-themed Poker game.

## Quick start (local)
1. Create a virtual environment and activate it:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # macOS / Linux
   .venv\Scripts\activate    # Windows (PowerShell)
   ```
2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Run locally:
   ```bash
   python app.py
   ```
4. Open browser at the printed URL (e.g. http://localhost:5001).

## Deploy to Render.com
1. Push this repository to GitHub.
2. Create a new Web Service on Render and connect your GitHub repo.
3. Set **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Ensure `requirements.txt`, `Procfile` and `runtime.txt` are present.
5. Add environment variable `SECRET_KEY` on Render for production.
6. Deploy and test the public URL.
