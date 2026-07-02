# Native Fetch Provider

Local HTTP fetch + readability extract provider for Hermes Agent.  
**No API key required.** Register as `web.extract_backend: native`.

## Usage

```yaml
# config.yaml
web:
  search_backend: ddgs       # or searxng, brave-free, etc.
  extract_backend: native    # local HTTP extraction, no API key

plugins:
  enabled:
    - web/native
```

Install dependencies:

```bash
pip install hermes-agent[native-fetch]
# or manually:
uv pip install readability-lxml html2text
```

## How It Works

1. Receives a URL to extract
2. Fetches the page via `httpx` (HTTP GET)
3. Extracts main content via Mozilla `readability-lxml`
4. Converts to clean Markdown via `html2text`
5. Returns structured result to the agent

Extract-only — pair with any search provider (`ddgs`, `searxng`, etc.).

## Configuration

All settings are controlled via environment variables (`HERMES_WEB_FETCH_*`).

| Variable | Default | Description |
|---|---|---|
| `HERMES_WEB_FETCH_TIMEOUT` | `30` | HTTP request timeout (seconds) |
| `HERMES_WEB_FETCH_MAX_REDIRECTS` | `5` | Maximum redirects to follow |
| `HERMES_WEB_FETCH_MAX_RESPONSE_BYTES` | `2000000` | Max raw response body size (bytes) |
| `HERMES_WEB_FETCH_MAX_CHARS` | `50000` | Max characters returned per URL |
| `HERMES_WEB_FETCH_MAX_CHARS_CAP` | `200000` | Absolute hard cap on returned chars |
| `HERMES_WEB_FETCH_CACHE_TTL` | `900` | Cache TTL (seconds, 15 min) |
| `HERMES_WEB_FETCH_READABILITY` | `true` | Enable Mozilla Readability extraction |
| `HERMES_WEB_FETCH_USE_TRUSTED_PROXY` | `false` | Use `HTTP_PROXY`/`HTTPS_PROXY` env vars |
| `HERMES_WEB_FETCH_USER_AGENT` | Chrome 131 UA | User-Agent header for HTTP requests |

## Dependencies

- `readability-lxml` — Mozilla Readability for main-content extraction
- `html2text` — HTML to Markdown conversion
- `httpx` — Async HTTP client (core Hermes dependency)

## Security

- SSRF protection via `async_is_safe_url` — blocks requests to private/internal networks
- URL secrets detection via `_PREFIX_RE` — blocks URLs containing API keys/tokens
- No JavaScript execution — static HTML only