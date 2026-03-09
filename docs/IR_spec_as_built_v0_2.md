# SemanticIR Spec v0.2 (As Built)

This document is the implementation contract for current code. It is intentionally descriptive, not aspirational.

## Scope

- Language: Python
- Entity kinds: function, async function, method, async method, class
- IR levels actively emitted: `L0`, `L1`, `L2`, `L3`
- Validation focus for this phase: `L0`, `L1`, `L3`

## Entity Prefixes

- `FN` function
- `AFN` async function
- `MT` method
- `AMT` async method
- `CLS` class

## L0

Format:

```text
[TYPE ENTITY_ID @file_path:start_line]
<raw source>
```

Purpose: exact verification surface.

## L1

Format:

```text
TYPE ENTITY_ID N=<name_token> [C=<calls>] [F=<flags>] [A=<assign_count>] [B=<bases>] [#DOMAIN] [#CATE]
```

Field semantics (fields are omitted when empty/zero):

- `N`: compressed name token (always present)
- `C`: semantic references (calls, plus class inheritance refs) — omitted if no calls
- `F`: sorted behavioral flags — omitted if no flags
- `A`: assignment operation count — omitted if zero
- `B`: base classes — omitted if no bases

Flags:

- `A` await encountered
- `E` raise encountered
- `I` conditional branch encountered
- `L` loop encountered
- `R` return encountered
- `T` try/except encountered
- `W` with/async-with encountered
- `X` exception-like class

## L3

Format:

```text
TYPE ENTITY_ID [#DOMAIN] #CATE
```

- `#DOMAIN`: uppercased domain if known
- `#CATE`: first 4 chars of category, uppercased

Note: The pattern fingerprint (`pattern_id`) is computed and stored in the index for change detection, but is not included in the text representation served to models (zero semantic signal at selection time).

Domains currently emitted from classifier signals:

- `#HTTP`, `#AUTH`, `#CRYP`, `#DB`, `#FS`, `#CLI`, `#ASYN`, `#PARS`, `#NET`

Categories currently emitted:

- `#CORE`, `#ROUT`, `#SCHE`, `#CONF`, `#COMP`, `#EXCE`, `#CONS`, `#TEST`, `#INIT`, `#DOCS`, `#UTIL`

## L2 Note

`L2` exists in code but is excluded from the current validation program. Including it now would mix harness issues with unresolved IR-design questions.

## Canonical Preambles

Use these files for all eval runners:

- `eval/preambles/l1_preamble.md`
- `eval/preambles/l3_preamble.md`

## Source of Truth

Machine contract: `docs/IR_contract_v0_2.json`
