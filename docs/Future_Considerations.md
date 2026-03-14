# CodeIR – Future Considerations

**Status:** Not part of v0.1

This document captures planned expansions, postponed ideas, and higher-complexity features intentionally excluded from v0.1.

These items are **not commitments**; they exist to prevent scope creep in the core spec.

---

## 1. Language-Specific Enhancements

### Async Patterns
- Detailed async/future/promise modeling
- Lambda/combinator expression compression
- Callback chains and event handlers

### Platform Integration
- Extended platform tags (e.g., `ios>=18.0`)
- UI interaction tags (UIKit, SwiftUI, React, Vue)
- Framework-specific annotations

### Advanced Language Features
- Treatment of decorators/annotations
- Handling macros and metaprogramming
- Template/generic expansion

---

## 2. Expanded Type System

### Advanced Types
- `GENERIC[T]`
- `UNION`
- `OPTIONAL`
- Algebraic data types

### Type Inference
- Structured type inference for typeless languages
- Type confidence heuristics exposed to the LLM
- Cross-language type mapping

---

## 3. Extended Temporal Events

Potential additions to the event system:

| Event Kind | Description |
|------------|-------------|
| `HOTFIX` | Emergency production fix |
| `PERF_REGRESSION` | Performance degradation detected |
| `API_BREAK` | Breaking API change |
| `SECURITY_RISK` | Security vulnerability introduced |
| `DEAD_CODE` | Unreachable or unused code |

---

## 4. Testing Awareness

Future attributes for test-related code:

```
attrs: {
  test: true
  fixture: true
  snapshot: true
}
```

### Capabilities
- Marking test suites, fixtures, and snapshots
- Test coverage mapping to production code
- Flaky test identification
- Test dependency graphs

---

## 5. Complexity and Metrics

Possible metrics to expose:

| Metric | Description |
|--------|-------------|
| **Cyclomatic complexity** | Control flow complexity |
| **Token count** | IR size in tokens |
| **Parameter threshold** | High parameter count warnings |
| **Fan-in / Fan-out** | Call graph metrics |
| **Instability factor** | Change frequency relative to dependencies |
| **Coupling metrics** | Inter-entity dependencies |

> **Note:** These metrics require deeper analysis and may impact performance.

---

## 6. IR Comparison and Merging

Full semantic diffing capabilities:

### Planned Features
- Control flow graph comparison
- Parameter drift analysis
- Churn analysis across versions
- Automatic change summaries
- IR → IR refactor suggestions
- Merge conflict resolution at IR level

### Use Cases
- Automated code review
- Refactoring validation
- Migration assistance
- Technical debt tracking

---

## 7. Cross-Language Normalization

Demonstrate language-agnostic compression with examples:

### Comparison Matrix
- **Python vs TypeScript vs Swift**
- **Kotlin vs Java**
- **C++ vs Rust**
- **Go vs JavaScript**

This requires additional normalizers in the compressor to handle:
- Different control flow constructs
- Language-specific idioms
- Standard library variations

---

## 8. Potential ID System Extensions

Beyond 4-letter identifiers:

### Options Under Consideration

**6-letter compressed forms**
```
FN_USRPRF
FN_USRPVD
```

**Hierarchical IDs**
```
FN_AUTH_USRM
FN_AUTH_SSNM
MD_SRVC_AUTH
```

**Semantic hashing**
```
FN_A7F3
CL_B8E2
```

These are optional future expansions for **large codebases** where 4-letter compression creates excessive collisions.

---

## 9. Enhanced Expansion Tools

### Planned Capabilities
- Partial expansion (show only changed lines)
- Side-by-side IR ↔ code view
- Interactive refactoring suggestions
- Real-time code preview during IR manipulation

---

## 10. Agent-Specific Enhancements

### Multi-Agent Coordination
- Shared IR index across agent teams
- Conflict detection when multiple agents modify same entities
- Locking mechanisms for IR modifications

### Learning and Adaptation
- Track which IR patterns lead to successful agent outcomes
- Optimize compression for frequently-accessed entities
- Adaptive abbreviation learning

---

## 11. Security and Privacy

### Planned Features
- PII detection in IR
- Sensitive code path marking
- Audit trail for IR expansions
- Access control for expansion tools

---

## 12. Performance Optimizations

### Future Work
- Incremental IR updates (avoid full recompilation)
- Parallel compression for large repositories
- IR caching strategies
- Lazy loading of temporal data

---

## Implementation Priority

These features will be prioritized based on:

1. **User feedback** from v0.1 adoption
2. **Agent performance** bottlenecks
3. **Real-world use cases** from production deployments
4. **Community contributions** and requests

---

## Contributing Ideas

Have suggestions for future enhancements? Please:

1. Check if the idea is already listed here
2. Open a GitHub discussion (not an issue)
3. Provide concrete use cases
4. Consider implementation complexity

**Remember:** The goal of v0.1 is simplicity. Complex features need compelling justification.

---

## Related Documentation

- [IR Specification v0.1](IR_spec.md) — Current stable spec
- [Main README](../README.md) — Project overview
- [Roadmap](../README.md#roadmap) — Implementation timeline
