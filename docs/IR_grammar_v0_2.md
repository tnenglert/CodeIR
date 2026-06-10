# CodeIR Grammar v0.2

Formal grammar for the CodeIR intermediate representation. Implementations in any language must produce output conforming to this grammar. AI tooling consuming any conforming implementation can treat the output as semantically equivalent.

Notation: Extended BNF. `[ x ]` = optional, `{ x }` = zero-or-more, `( x | y )` = alternation, `"x"` = literal terminal.

---

## Top-Level Records

Each IR output consists of one or more records, one per entity, each on its own line (or block for Source level).

```bnf
ir-output       ::= { record LF }

record          ::= source-record
                  | behavior-record
                  | index-record
```

---

## Shared Terminals

```bnf
; Entity kind prefix — exactly one of:
entity-type     ::= "FN"            ; function                  (Python, Rust, TypeScript)
                  | "AFN"           ; async function             (Python, TypeScript)
                  | "MT"            ; method                     (Python, Rust impl, TypeScript)
                  | "AMT"           ; async method               (Python, TypeScript)
                  | "TMT"           ; trait method               (Rust)
                  | "CLS"           ; class                      (Python, TypeScript)
                  | "ST"            ; struct                     (Rust)
                  | "EN"            ; enum                       (Rust, TypeScript)
                  | "TRT"           ; trait                      (Rust)
                  | "IFC"           ; interface                  (TypeScript)
                  | "TYP"           ; type alias                 (TypeScript)
                  | "NS"            ; namespace / module         (TypeScript)
                  | "CST"           ; constant                   (Rust, TypeScript)
                  | "ENT"           ; fallback for unrecognised kinds

; Compact stem with optional collision suffix.
; Derived from the entity's name by the compact-stem algorithm (see § Compact Stem).
entity-id       ::= stem [ "." suffix ]
stem            ::= UPPER { UPPER }           ; 2+ uppercase ASCII letters (no max length)
suffix          ::= DIGIT DIGIT               ; 02, 03, ... for collision resolution

; Separator
SP              ::= " "             ; U+0020, single space
LF              ::= "\n"            ; U+000A
COMMA           ::= ","
UPPER           ::= "A" | "B" | ... | "Z"
DIGIT           ::= "0" | ... | "9"
```

---

## Source Level

```bnf
source-record   ::= source-header LF raw-source

source-header   ::= "[" entity-type SP entity-id SP "@" file-path ":" start-line "]"

file-path       ::= path-char { path-char }
path-char       ::= any printable ASCII except "]" and ":"

start-line      ::= DIGIT { DIGIT }           ; 1-based line number

raw-source      ::= source-line { LF source-line }
source-line     ::= any-unicode-char { any-unicode-char }
                  ; exact source text of the entity, including leading whitespace
```

**Constraints:**
- `file-path` is relative to the repository root, using forward slashes.
- `start-line` is the line where the entity definition begins.
- `raw-source` is the verbatim text of the entity; no transformation applied.

**Examples:**
```
[FN LGNRQRD @examples/tutorial/flaskr/auth.py:19]
def login_required(view):
    ...

[AMT ATHNTCT.02 @fastapi_users/manager.py:636]
async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> Optional[models.UP]:
    ...
```

---

## Behavior Level

```bnf
behavior-record ::= entity-type SP entity-id
                    [ SP calls-field ]
                    [ SP flags-field ]
                    [ SP assign-field ]
                    [ SP bases-field ]
                    [ SP domain-tag ]
                    [ SP cate-tag ]

; C= — semantic call/reference list (max 6 callees, in extraction order)
calls-field     ::= "C=" call-ref { COMMA call-ref }
call-ref        ::= ref-char { ref-char }
ref-char        ::= UPPER | LOWER | DIGIT | "_"   ; abbreviated or verbatim callee name
LOWER           ::= "a" | "b" | ... | "z"

; Note: calls are listed in source order (order of appearance in AST traversal),
; limited to the first 6 callees. This preserves the entity's control flow signal.

; F= — behavioral flags, alphabetically sorted, concatenated, no separator
flags-field     ::= "F=" flag { flag }
flag            ::= "A"    ; await encountered
                  | "E"    ; raise encountered
                  | "I"    ; conditional branch (if/elif/else)
                  | "L"    ; loop (for/while)
                  | "R"    ; return encountered
                  | "T"    ; try/except encountered
                  | "W"    ; with / async-with encountered
                  | "X"    ; exception-like class (extends Exception hierarchy)

; A= — assignment count
assign-field    ::= "A=" pos-int
pos-int         ::= DIGIT { DIGIT }               ; value > 0; omit field if zero

; B= — base classes (classes only)
bases-field     ::= "B=" base-ref { COMMA base-ref }
base-ref        ::= ref-char { ref-char }
```

**Omission rules (normative):**
- `calls-field` is omitted when the entity makes no calls or references.
- `flags-field` is omitted when no flags apply.
- `assign-field` is omitted when assignment count is zero.
- `bases-field` is omitted when the entity has no base classes, or is not a class.
- Fields are never emitted as empty placeholders (e.g. `C=` with no value is not valid).

**Field order is fixed:** `C=` → `F=` → `A=` → `B=` → `#DOMAIN` → `#CATE`

**Examples:**
```
FN LGNRQRD C=RDIR,url_for,view,wraps F=IR #AUTH #ROUT
CLS BLPRNT C=AppGroup,RuntimeError,SansioBlueprint,ValueError,CINIT,cast F=EIR A=5 B=SansioBlueprint #CORE
AMT ATHNTCT.02 C=get_by_email,hash,update,verify_and_update F=AIRT A=2 #AUTH #CORE
CLS INVLDPSSWRDX C=FastAPIUsersException F=X A=1 B=FastAPIUsersException #EXCE
```

---

## Index Level

```bnf
index-record    ::= entity-type SP entity-id
                    [ SP domain-tag ]
                    SP cate-tag
```

**Constraints:**
- `domain-tag` is omitted when the domain is `unknown` (indexer failure) or `misc` (classifier ran cleanly but found no specific domain).
- `cate-tag` is always present.

**Examples:**
```
FN LGNRQRD #AUTH #ROUT
CLS BLPRNT #CORE
AMT ATHNTCT.02 #AUTH #CORE
MT VRFYNDPDT.02 #AUTH #CORE
```

---

## Classification Tags

```bnf
domain-tag      ::= "#" domain-token
cate-tag        ::= "#" cate-token

domain-token    ::= "HTTP"  | "AUTH"  | "CRYPTO" | "DB"    | "FS"
                  | "CLI"   | "ASYNC" | "PARSE"  | "NET"
                  | "UI"    | "VALID" | "I18N"   | "TASK"  | "EVENT"
                  | "LOG"   | "MAIL"  | "MEDIA"  | "ADMIN" | "CACHE"
                  ; "unknown" and "misc" are not emitted as compressed IR tags

cate-token      ::= "CORE" | "ROUT" | "SCHE" | "CONF" | "COMP"
                  | "EXCE" | "CONS" | "TEST" | "INIT" | "DOCS" | "UTIL"

; Category tokens are derived by truncating the internal category name to 4 characters
; and uppercasing: "core_logic" → "CORE", "exceptions" → "EXCE", "router" → "ROUT"
```

**Semantics:**

| Domain token | Concept |
|---|---|
| `#HTTP` | HTTP request/response handling |
| `#AUTH` | Authentication and authorization |
| `#CRYPTO` | Cryptography and hashing |
| `#DB` | Database access and ORM |
| `#FS` | Filesystem I/O |
| `#CLI` | Command-line interface |
| `#ASYNC` | Async/concurrency primitives |
| `#PARSE` | Parsing and serialization |
| `#NET` | Network (non-HTTP) |
| `#UI` | User interface (templates, GUI) |
| `#VALID` | Validation and schema enforcement |
| `#I18N` | Internationalization and localization |
| `#TASK` | Background tasks and job queues |
| `#EVENT` | Event dispatch and signals |
| `#LOG` | Logging and observability |
| `#MAIL` | Email delivery |
| `#MEDIA` | Media processing and file storage |
| `#ADMIN` | Admin interfaces |
| `#CACHE` | Caching layers |

| Category token | Concept |
|---|---|
| `#CORE` | Core business logic |
| `#ROUT` | Routing and dispatch |
| `#SCHE` | Schema and data models |
| `#CONF` | Configuration |
| `#COMP` | Compatibility shims |
| `#EXCE` | Exception definitions |
| `#CONS` | Constants |
| `#TEST` | Test code |
| `#INIT` | Module initialization |
| `#DOCS` | Documentation helpers |
| `#UTIL` | Utilities |

Tags are derived from classifier signals applied to the entity's module path, name tokens, and call graph. An entity may carry at most one domain tag and exactly one category tag in emitted text.

---

## Compact Stem Algorithm

The `entity-id` is a compact, uppercase, consonant-biased abbreviation of the entity's leaf name (the last component after any `.` separator). Implementations must apply the following algorithm to ensure stable, collision-aware IDs:

1. **Normalize:** strip all non-alphanumeric characters (underscores, hyphens, dots, etc.) from the name to produce a single combined string.
2. **Short-name fast path:** if the combined string is ≤4 characters, uppercase it as-is and use it as the stem (e.g. `send` → `SEND`).
3. **Vowel-strip:** for longer names, keep the first character unchanged and strip interior vowels (`a e i o u`, case-insensitive) from the remaining characters. Uppercase the result.
4. **Truncate** to a maximum of 12 characters from the left.
5. **Collision resolution:** if two entities in the same repository produce the same stem, append a dot and zero-padded 2-digit suffix to the second and subsequent collisions (e.g. `RQST`, `RQST.02`, `RQST.03`).

Note: the algorithm operates on the combined name string, not on individual `_`-split tokens. There is no camelCase splitting.

**Examples:**

| Original name | Tokens | After vowel-strip | Stem |
|---|---|---|---|
| `login_required` | `login`, `required` | `lgn`, `rqrd` | `LGNRQRD` |
| `Blueprint` | `Blueprint` | `Blprnt` | `BLPRNT` |
| `response` | `response` | `rspns` | `RSPNS` |
| `url_defaults` | `url`, `defaults` | `url`, `dflts` | `URLDFLTS` |
| `_split_blueprint_path` | `split`, `blueprint`, `path` | `splt`, `blprnt`, `pth` | `SPLTBLPRNTPT` |
| `send_static_file` | `send`, `static`, `file` | `snd`, `sttc`, `fl` | `SNDSTTCFL` |
| `test_basic_view` | `test`, `basic`, `view` | `tst`, `bsc`, `vw` | `TSTBSCVW` |

---

## Internal Fields (Not Emitted)

These fields are computed by implementations and stored in the backing store for operational use, but are **never included in the text output** served to AI tooling:

| Field | Purpose |
|---|---|
| `pattern_id` | Structural fingerprint hash for change detection and drift tracking |

---

## Conformance Requirements

A conforming implementation MUST:

1. Emit records using exactly the field order and separators defined here.
2. Omit optional fields when their value is empty or zero — never emit `C=`, `F=`, `A=`, `B=` with empty values.
3. Sort flags within `F=` alphabetically.
4. Emit calls within `C=` in source order (AST traversal order), limited to 6 callees.
5. Apply the compact-stem algorithm and resolve collisions within the repository scope.
6. Use `#` prefix for all classification tags.
7. Use single spaces as field separators; no trailing whitespace on lines.
8. Emit file paths relative to the repository root using forward-slash separators on all platforms.

A conforming AI consumer MUST:

1. Treat `entity-type + entity-id` as the stable primary key for an entity.
2. Prefer Behavior or Index records for candidate selection; use Source only for verification or patch drafting.
3. Not assume the order of records in a multi-entity output carries semantic meaning.
4. Treat an absent field as equivalent to "zero / none" — not as unknown.

---

## Extension Points (Non-Normative)

Implementations may compute and surface derived metadata alongside IR records. These signals are not part of the canonical grammar and must not affect conformance. They may appear in CLI output, tool responses, or auxiliary fields.

Examples of derived metadata:

| Signal | Purpose |
|---|---|
| Caller/callee counts | Dependency density for impact estimation |
| Risk or complexity scores | Change safety heuristics |
| Suggested next actions | Workflow guidance (e.g., "check callers before modifying") |

The canonical IR layer documents **facts** (what exists, what it calls, what flags apply). Derived signals interpret those facts for decision-making. Keeping these layers separate allows the grammar to remain stable while implementations experiment with richer tooling.

---

## Version

This grammar describes CodeIR `v0.2`. The machine-readable contract is `docs/IR_contract_v0_2.json`.
