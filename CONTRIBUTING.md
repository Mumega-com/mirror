# Contributing to Mirror

Mirror is a small, focused project. Contributions are welcome — bug fixes, new backends, better docs, performance improvements.

## Running locally

**Requirements:** Python 3.11+, PostgreSQL with pgvector extension

```bash
# Clone
git clone https://github.com/Mumega-com/mirror.git
cd mirror

# Install deps
pip install fastapi uvicorn psycopg2-binary python-dotenv google-genai pydantic ruff

# Set up the database
createdb mirror
psql mirror < schema.sql

# Copy and fill in env vars
cp .env.example .env
# You need: GEMINI_API_KEY, DATABASE_URL, MIRROR_ADMIN_TOKEN

# Run
python mirror_api.py
```

The API will be at `http://localhost:8844`. You can test with curl or hit `http://localhost:8844/docs` for the auto-generated Swagger UI.

## Running the linter

```bash
ruff check . --select E,F,I,W --ignore E501
```

## Making a PR

1. Fork the repo and create a branch off `main`
2. Make your changes
3. Run the linter — PRs with lint errors will be asked to fix before merge
4. Open a PR with a clear description of what changed and why
5. Keep PRs focused — one logical change per PR

## What's in scope

- Bug fixes (always welcome)
- New DB backends (MySQL, SQLite, etc.)
- Performance improvements to search or indexing
- Better test coverage
- Documentation improvements

## What's out of scope

- Changing the core API shape without discussion — other systems depend on it
- Adding heavy dependencies
- Replacing pgvector with a different vector store without strong justification

## Questions

Open an issue. We'll respond.
