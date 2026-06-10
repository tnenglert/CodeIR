# CodeIR Spec v0.2 (As Built)

This document is the implementation contract for current code. It is intentionally descriptive, not aspirational.

## Scope

- Languages: Python, Rust, TypeScript / TSX
- Entity kinds: see per-language tables below
- IR levels actively emitted: `Source`, `Behavior`, `Index`

---

## Entity Prefixes

Entity IDs have the form `TYPE.STEM` or `TYPE.STEM.NN` when siblings collide.

| Prefix | Kind | Languages |
|--------|------|-----------|
| `FN`  | function | Python, Rust, TypeScript |
| `AFN` | async function | Python, TypeScript |
| `MT`  | method | Python, Rust (impl), TypeScript |
| `AMT` | async method | Python, TypeScript |
| `TMT` | trait method | Rust |
| `CLS` | class | Python, TypeScript |
| `ST`  | struct | Rust |
| `EN`  | enum | Rust, TypeScript |
| `TRT` | trait | Rust |
| `IFC` | interface | TypeScript |
| `TYP` | type alias | TypeScript |
| `NS`  | namespace | TypeScript |
| `CST` | constant | Rust, TypeScript (const/let arrow) |

Fallback prefix for unrecognised kinds: `ENT`.

---

## Source

Format:

```text
[TYPE ENTITY_ID @file_path:start_line]
<raw source>
```

Purpose: exact verification surface. Use only when you need to edit or verify implementation.

---

## Behavior

Format:

```text
TYPE ENTITY_ID [C=<calls>] [F=<flags>] [A=<assign_count>] [B=<bases>] [#DOMAIN] [#CATE]
```

Fields are omitted when empty or zero.

| Field | Meaning | Omitted when |
|-------|---------|--------------|
| `C=`  | Semantic references: calls + inheritance refs | no calls |
| `F=`  | Sorted behavioral flags (see table below) | no flags |
| `A=`  | Assignment / field count | zero |
| `B=`  | Base classes or derived traits | no bases |
| `#DOMAIN` | Uppercased domain tag | domain is `unknown` |
| `#CATE` | First 4 chars of category, uppercased | never omitted |

Note: `N=` (name token) was removed in v0.2 — redundant with entity ID which already carries the semantic abbreviation.

### Behavioral flags

| Flag | Meaning |
|------|---------|
| `A`  | `await` / `.await` encountered |
| `E`  | raise / throw / `?` error propagation encountered |
| `I`  | conditional branch (`if`/`match`/ternary) encountered |
| `L`  | loop (`for`/`while`/`loop`) encountered |
| `R`  | return / `return` value encountered |
| `T`  | try/except / `catch` block encountered |
| `W`  | with / `using` / resource guard encountered |
| `X`  | exception-like class (name ends in `Error`/`Exception`, or Rust error enum) |

### Language notes

**Rust structs and enums:** `A=` counts field declarations for structs and variant count for enums. Flags are minimal (only `X` for error enums). Calls come from `impl` block methods, not the struct definition itself.

**TypeScript:** `A=` counts variable declarators and assignment expressions within the function body. Interface `B=` captures `extends` clauses.

---

## Index

Format:

```text
TYPE ENTITY_ID [#DOMAIN] #CATE
```

| Field | Meaning |
|-------|---------|
| `#DOMAIN` | Uppercased domain; omitted if domain is `unknown` |
| `#CATE`   | First 4 chars of category, uppercased |

Note: the pattern fingerprint (`pattern_id`) is computed and stored for change detection but is not included in the text representation served to models.

---

## Domains

Domains are assigned by `classify_domain()` in `ir/classifier.py`. One special value is not emitted as a tag:

- `unknown` — indexer failure: parse error, missing AST tree, or file skipped

`misc` means `classify_domain()` ran cleanly but no specific domain signal applied (cross-cutting, glue code, boilerplate). In compressed Behavior and Index IR, `misc` is omitted like a missing domain tag to save tokens; it remains visible in module-level summaries such as bearings.

| Tag | Domain | Signals |
|-----|--------|---------|
| `#HTTP` | http | requests, httpx, aiohttp, flask, fastapi, urllib |
| `#AUTH` | auth | jwt, oauth, passlib, authlib, bcrypt, filename: auth/login |
| `#CRYPTO` | crypto | cryptography, nacl, hashlib direct use |
| `#DB` | db | sqlalchemy, django.db, alembic, peewee, tortoise, redis |
| `#FS` | fs | pathlib, shutil, os.path, aiofiles |
| `#CLI` | cli | argparse, click, typer, rich, sys.argv |
| `#ASYNC` | async | asyncio, trio, anyio, concurrent.futures |
| `#PARSE` | parse | json, xml, yaml, toml, csv, html.parser |
| `#NET` | net | socket, ssl, ipaddress, struct for packets |
| `#UI` | ui | jinja2, django templates, tkinter, curses |
| `#VALID` | validation | pydantic validators, marshmallow, wtforms, cerberus |
| `#I18N` | i18n | gettext, babel, django.utils.translation |
| `#TASK` | task | celery, rq, dramatiq, apscheduler, arq |
| `#EVENT` | event | django signals, blinker, pyee, filename: events/signals |
| `#LOG` | log | logging, structlog, loguru, opentelemetry |
| `#MAIL` | mail | smtplib, sendgrid, mailgun, django.core.mail |
| `#MEDIA` | media | pillow, ffmpeg-python, boto3 for S3 uploads |
| `#ADMIN` | admin | django.contrib.admin, filename: admin/ |
| `#CACHE` | cache | django.core.cache, aiocache, cachetools |

---

## Categories

Categories are assigned by `classify_file()` in `ir/classifier.py` using a four-stage pipeline:

1. **Filename patterns** — `__init__.py` → `init`, `test_*.py` → `tests`, `conftest.py` → `tests`, `constants.py` → `constants`, `exceptions.py` → `exceptions`, `config.py` / `settings.py` → `config`
2. **Directory patterns** — `tests/` → `tests`, `config/` → `config`, `schemas/` → `schema`, `services/` → `core_logic`
3. **AST structural analysis** — route decorators → `router`, BaseModel/Schema dominance → `schema`, compat imports → `compat`, exception-only classes → `exceptions`, assigns-only → `constants`, docstring-only → `docs`
4. **Count-based fallback** — ≤3 defs → `utils`, otherwise → `core_logic`

Stage can be queried via `classify_file_with_stage(path, tree) -> (category, stage)` where stage is 1–4.

| Tag | Category |
|-----|----------|
| `#CORE` | core_logic |
| `#ROUT` | router |
| `#SCHE` | schema |
| `#CONF` | config |
| `#COMP` | compat |
| `#EXCE` | exceptions |
| `#CONS` | constants |
| `#TEST` | tests |
| `#INIT` | init |
| `#DOCS` | docs |
| `#UTIL` | utils |

---

## Module IR lines (bearings)

Module-level lines appear in `bearings.md`, not in entity IR rows. Format:

```text
filename.py  cat:<category>  entities:<N>  [dom:<domain>]  [deps:<dep1,dep2>]
```

Common filenames (e.g., `utils.py`) include the parent directory for disambiguation. `dom:` is omitted when domain is `unknown`. `deps:` is omitted when no internal dependencies.

---

## ID encoding

Entity IDs are deterministic and stable across re-indexing as long as the entity's qualified name and kind do not change.

**STEM generation** (`compact_stem` in `ir/stable_ids.py`):
- Names ≤4 chars: preserved as-is, uppercased (e.g., `send` → `SEND`)
- Names >4 chars: vowels stripped after the first character, max 12 chars (e.g., `process_payment` → `PRCSPYMNT`)

**Collision suffix**: when two entities produce the same STEM within the same kind, a two-digit suffix `.01`, `.02`, … is appended.

**Full stable ID**: `TYPE.STEM` or `TYPE.STEM.NN` (e.g., `AMT.RDTKN.03`). Type prefix is stored separately in the row; the displayed ID omits the prefix in search/show output but includes it in `expand` and `callers` output.

---

## Compression levels

| Level | Emitted by default | Use case |
|-------|--------------------|----------|
| `Behavior` | yes (default) | task relevance ranking, code navigation |
| `Index` | yes (default, `Behavior+Index`) | module/entity selection, orientation |
| `Source` | no (opt-in via `--level all`) | pre-edit verification |

Default config: `compression_level = "Behavior+Index"`.

---

## Token budget (tiktoken cl100k_base, measured)

From `scripts/measure_compression.py` run against five production codebases:

| Codebase | Entities | Source tokens | Behavior tokens | Ratio |
|----------|----------|---------------|-----------------|-------|
| MiroFish | 587 | 264,412 | 16,816 | 15.7× |
| Flask | 1,624 | 185,002 | 37,861 | 4.9× |
| Tryton | 20,457 | 3,378,504 | 557,707 | 6.1× |
| SQLAlchemy | 38,923 | 7,798,031 | 981,666 | 7.9× |
| Django | 41,941 | 6,943,900 | 1,121,446 | 6.2× |

**Note on the 4-char estimate:** The `chars // 4` approximation is within ~15% of reality for source-level text but is systematically ~25–30% optimistic for Behavior and Index levels. Behavior IR uses dense short tokens (`MT`, `C=`, `#DB`) that tokenize to fewer chars-per-token than prose. Use the tiktoken-measured values above for any published compression claims.

---

## Source of truth

Machine contract: `docs/IR_contract_v0_2.json`

Measurement script: `scripts/measure_compression.py`
