# Contributing — quantsignal-api

## Architecture Rules (enforced, not optional)

### 1. Route files are HTTP-only
`api/routes/*.py` files may only: validate input, call domain, return response.
- ✅ `signal = generate_signal(symbol)`
- ❌ Business logic, DB queries, or heavy computation inside a route function

### 2. No FastAPI imports under domain/
`domain/` is pure Python business logic. No `Request`, `Response`, `APIRouter`, `Depends`.
- ✅ `from app.domain.signal.service import generate_signal`
- ❌ `from fastapi import HTTPException` inside domain/

### 3. cron.py delegates to tasks.py
`cron.py` only accepts HTTP and calls `tasks.py`. It does not execute rebuild logic directly.

### 4. No new code in signals_ext.py
`signals_ext.py` is deleted. Use `market_context.py` or `signal_stream.py`.

### 5. infrastructure/ handles external adapters only
Cache, DB, queues. No business logic here.

## File Size Limits
| Type | Max lines |
|---|---|
| Route file | ~250 |
| Domain service | ~300 |
| tasks.py | ~300 |
| Any new file | ~250 |

If a file exceeds this, split it before adding more features.

## Before every PR
- No business logic in route files
- No FastAPI imports in domain/
- No new endpoints added to a file that already has 5+ endpoints — create a new route file
