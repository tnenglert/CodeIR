# CodeIR

CodeIR compiles Python codebases into a hierarchical semantic representation that fits in an LLM's context window. Instead of reading source files, your coding agent navigates architecture.

Index a repo once. Your agent sees every entity, every call relationship, every module boundary Ñ before reading a single line of source.

## Why

When Claude or GPT tries to understand a codebase, it reads files. Entire files. Thousands of lines of syntax, indentation, and formatting, most of which carries no architectural signal. A typical Python file is 80%+ syntax noise from the perspective of system understanding.

CodeIR compresses that away. It extracts the structural and behavioral information an LLM actually needs and organizes it into three levels of progressive detail:

| Level | What it contains | When to use it |
|---|---|---|
| **Index** | Entity names, types, locations, categories | Orientation Ñ what exists and where |
| **Behavior** | Call graphs, flags, assignments, domain tags | Understanding Ñ what it does without reading source |
| **Source** | Raw source code for a single entity | Verification Ñ read only what you need to edit |

## Compression

| Repository | Entities | Raw Python tokens* | Index tokens | Compression |
|---|---|---|---|---|
| Flask | 1,629 | ~148k | 19k | 8:1 |
| Tryton | 20,457 | ~2.8M | 214k | 13:1 |
| SQLAlchemy | 38,672 | ~5.0M | 467k | 11:1 |
| Django | 41,819 | ~4.7M | 475k | 10:1 |

*Estimated. 1 token per 4 characters.

At Index level, CodeIR fits roughly 20,000 entities in a 200k context window. The same window holds fewer than 2,000 entities as raw source Ñ and only if you could perfectly select which files to load.

## Installation

```bash
pip install git+https://github.com/tnenglert/CodeIR.git
```

## Quick Start

Index a repository:

```bash
codeir index /path/to/your/repo
```

Get oriented:

```bash
codeir bearings                    # project summary + category menu
codeir bearings core_logic         # drill into a specific category
```

Find entities:

```bash
codeir search "flush"              # search by name
codeir search "session" --category core_logic  # filter by category
codeir grep "listonly" --path orm/  # regex search grouped by entity
```

Inspect without reading source:

```bash
codeir show FLSH.04                # Behavior-level: what it does and calls
codeir show FLSH.04 --level Index  # Index-level: just the basics
```

Read source when you need it:

```bash
codeir expand FLSH.04              # raw source for this entity only
```

Understand dependencies:

```bash
codeir trace FLSH.04 AFTRFLSHPSTXC  # shortest static call path between two entities
codeir callers FLSH.04             # what calls this entity
codeir impact FLSH.04 --depth 2    # reverse dependency analysis
codeir scope FLSH.04               # callers + callees + sibling methods
```

## Integration with Claude Code

Add a `codeir.md` file to `.claude/rules/` in your repository. Claude Code reads this automatically at session start. A template is included at `templates/codeir.md` or you can generate one:

```bash
codeir init                        # uses repo markers, then falls back to current runtime
codeir init --platform current     # generate only for the active runtime
codeir init --platform all         # generate for Claude Code, Codex, and OpenClaw
```

Once integrated, Claude Code uses CodeIR commands instead of reading raw files Ñ searching the IR, inspecting behavior summaries, and expanding only the source it needs.

## Reading Behavior IR

Behavior summaries use a compressed notation:

```
MT FNLZFLSHCHNGS C=_register_persistent,_remove_newly_deleted,difference,items,set F=IR A=3 #DB #CORE
```

| Field | Meaning |
|---|---|
| `MT` | Method (also: `FN` function, `CLS` class, `AMT` async method) |
| `C=` | Calls made |
| `F=` | Flags: `R` returns, `E` raises, `I` conditionals, `L` loops, `T` try/except, `W` with |
| `A=` | Assignment count |
| `B=` | Base class |
| `#TAG` | Domain tags (e.g., `#DB`, `#CORE`, `#CLI`) |

## How It Works

CodeIR statically analyzes Python source using the AST. For each entity (function, class, method), it extracts:

- **Identity**: name, type, file, line number, category
- **Behavior**: outbound calls, control flow flags, assignment count, domain tags
- **Relationships**: imports (strong/weak tiering), caller chains (3-tier resolution)

Entities are assigned compressed IDs (e.g., `FLSH.04`) and organized by auto-detected category. The index is stored in `.codeir/` and served through the CLI.

The representation is designed for progressive disclosure: orient at Index level, understand at Behavior level, verify at Source level. An agent should rarely need to read source for entities it isn't planning to modify.

## Links

- **Blog**: [codeir.dev](https://codeir.dev)
- **Issues & Discussion**: [GitHub Discussions](https://github.com/tnenglert/CodeIR/discussions)
