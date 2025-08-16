# Coda AI Analysis Service

Render-based service for processing large content through Claude API without timeout constraints.

## Deployment

1. Push to GitHub
2. Connect to Render.com
3. Add `CLAUDE_API_KEY` environment variable
4. Services auto-deploy via render.yaml

## Architecture

- **Web Service**: FastAPI endpoint for analysis requests
- **Worker Service**: Background processing with chunking
- **Queue**: Redis-compatible job management

## Usage

POST to `/analyze` with:
```json
{
  "record_id": "coda-record-id",
  "content": "content to analyze",
  "webhook_url": "https://coda.io/hooks/...",
  "prompt_config": { ... },
  "is_iteration": false
}
```

Results delivered via webhook to Coda.
