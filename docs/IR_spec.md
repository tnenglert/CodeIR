# SemanticIR Specification (v0.1)

This document defines the initial intermediate representation ("IR") used by SemanticIR.

**Version 0.1** prioritizes simplicity, determinism, and LLM-friendliness over completeness.

The IR captures structural semantics of code without retaining raw syntax. LLMs operate on the IR exclusively unless expansion tools are explicitly invoked.

---

## 1. Core Principles

### IR as Primary Representation
Raw code is treated as an I/O layer. The LLM operates entirely on IR.

### Deterministic Generation
Given identical source, the compressor must produce the same IR output.

### Stable Entity Identifiers
Functions, classes, methods, and modules receive stable IDs derived from compressed names with deterministic collision resolution.

### Abbreviation-First Naming
Names are canonicalized through:
- **CORE_MAP** (global abbreviations)
- **Local abbreviation maps**
- **4-letter compressed identifiers** with suffixes for collisions

### Structure, Not Syntax
The IR represents meaning: calls, control flow, imports, attributes, relationships.

---

## 2. Entity Types

**Supported in v0.1:**
- `FUNCTION`
- `METHOD`
- `CLASS`
- `MODULE`

### Entity Structure

Each entity includes:

| Field | Description |
|-------|-------------|
| `id` | Stable identifier |
| `kind` | Entity type |
| `name` | Compressed canonical name |
| `params` | List of parameter symbols (compressed) |
| `returns` | Type or VOID (types optional in v0.1) |
| `attrs` | Structural attributes |
| `body` | IR statements |
| `temporal` | Optional metadata when temporal mode is enabled |

---

## 3. Identifier Format (Stable IDs)

Stable IDs use a **prefix + four-letter compressed name + optional collision counter**.

### Examples

```
FN_XXXX
FN_XXXX_02
MT_XXXX
CL_XXXX
MD_XXXX
```

### Prefix Convention

| Prefix | Meaning |
|--------|---------|
| `FN_` | Function |
| `MT_` | Method |
| `CL_` | Class |
| `MD_` | Module |

### Collision Resolution (v0.1)

If multiple entities compress to the same four letters:

1. Sort candidates by **declaration order** within the file
2. First occurrence gets `XXXX`
3. Subsequent collisions get `_02`, `_03`, etc., in declaration order

This rule is **deterministic and language-agnostic**.

---

## 4. Abbreviation Rules

### 4.1 CORE_MAP (Global)

Common domain terms mapped to stable abbreviations.

**Example subset:**

| Term | Abbreviation |
|------|--------------|
| user | USR |
| account | ACCT |
| session | SESS |
| config | CFG |
| error | ERR |
| request | REQ |
| response | RES |

> Exact definitions maintained in `abbreviations.py`

### 4.2 Local Map (Repository-Level)

For names not in CORE_MAP:

1. Strip vowels except the first
2. Truncate to 4 characters
3. Uppercase
4. Resolve collisions via suffix (`_02`, etc.)

**Examples:**

```
UserProfile  → USRP
UserProvider → USRP_02
```

---

## 5. Type Handling (v0.1)

Types are **optional** and compressed only if the language exposes them directly.

### Allowed Forms in v0.1

- `INT`
- `STR`
- `BOOL`
- `OBJ`
- `LIST`
- `DICT`
- `VOID`

Languages without explicit types may use `VOID` or omit the field.

---

## 6. IR Statement Syntax

IR statements are compact and line-based:

```
CALL <ENTITY_ID> (<ARGS>)
RETURN <EXPR>
ASSIGN <VAR> <EXPR>
IF <COND> THEN ...
LOOP <VAR> IN <ITER>
RAISE <ERR>
```

Expressions are **symbolic, not raw syntax**.

### Examples

```
CALL FN_DBQY (USR_ID)
IF ERR THEN RAISE ERR
RETURN RES
```

### Complex Expression Examples (v0.1)

```
EXPR ADD(A, B)
EXPR NOT(COND)
EXPR TERN(COND, A, B)
CALL FN_AUTH (USR, PASS)
```

Nested expressions are allowed but should remain **shallow**.

---

## 7. Modules

Modules collect top-level information without storing file paths or raw code.

### Example

```
MD_AUTH {
  kind: MODULE
  name: AUTH
  imports: [ JWT, DB, ENV ]
  exports: [ FN_VFYR, FN_SSNM ]
}
```

---

## 8. Structural Attributes

Each entity may include:

```
attrs: {
  async: true|false
  stateful: true|false
  platform: [ ios, android, web ]    (optional)
  calls: [ <ENTITY_ID>, ... ]
  imports: [ <symbol>, ... ]
}
```

### Stateful Definition (v0.1)

An entity is considered **stateful** if it:

- Mutates class or instance fields
- Writes to global or module-level variables
- Modifies captured closures

> **Note:** Statefulness does not include local variable mutation.

---

## 9. Error-Handling Representation (v0.1)

Errors are represented through:

```
RAISE <ERR_SYMBOL>
CATCH <ERR_SYMBOL> THEN ...
```

### Example

```
CALL FN_NETW (URL)
IF ERR THEN RAISE NET_ERR
```

---

## 10. Temporal Metadata (Optional)

When temporal mode is enabled:

```
temporal: {
  change_count: <int>
  last_changed: <timestamp>
  events: [
    { t: <ts>, kind: CREATED },
    { t: <ts>, kind: SIG_CHANGED, details: "added TIMEOUT" },
    { t: <ts>, kind: MOVED, file: "services/auth.py" }
  ]
}
```

### Supported Event Kinds (v0.1)

| Event Kind | Description |
|------------|-------------|
| `CREATED` | Entity first introduced |
| `MOVED` | File relocation |
| `SIG_CHANGED` | Function signature modified |
| `BODY_CHANGED` | Implementation updated |
| `EXTRACTED` | Refactored from another entity |
| `PARAM_ADDED` | New parameter added |
| `PARAM_REMOVED` | Parameter removed |
| `CHURN_SPIKE` | Abnormally high change frequency |
| `DEPRECATED` | Marked as deprecated |
| `SECURITY_FIX` | Security-related change |

---

## 11. IR Diffing (Minimal v0.1)

SemanticIR compares entities by:

- Stable ID
- Parameter list
- Attribute set (async, stateful, platform, calls)
- IR body lines

Differences are returned as **structured changes, not textual diffs**.

---

## 12. Limitations (v0.1)

This version intentionally excludes:

-  Metaprogramming, decorators, macros
-  Direct UI framework modeling
-  Cyclomatic complexity or deep analysis
-  Cross-language equivalence guarantees (future version)

---

## 13. Versioning

**This is v0.1** — minimal, stable, intentionally narrow.

- **Backward-compatible improvements** increment `v0.x`
- **Breaking changes** increment `v1.x`

---

## Related Documentation

- [Main README](../README.md) — Project overview
- [Future Considerations](Future_Considerations.md) — Planned expansions
- [Temporal Design](temporal_design.md) — Detailed temporal tracking
- [Integration Examples](integration_examples.md) — Agent usage patterns
