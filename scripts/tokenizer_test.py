#!/usr/bin/env python3
"""Test token cost of different separator characters in entity IDs.

Tests underscore (current), dot, dash, and mixed separators across:
- tiktoken cl100k_base (GPT-4, Claude)
- tiktoken o200k_base (GPT-4o)
"""

import tiktoken

# Representative entity IDs from fastapi-users index
SAMPLE_IDS = [
    "AMT_WRTTKN_02",
    "AFN_GTSRDB",
    "FN_MCKSRDB",
    "FN_TSTPP",
    "AMT_TSTNVLDTKN_12",
    "AMT_UPDTTHCCNT",
    "CLS_BASE_05",
    "AMT_GTTSTCLNT",
    "AMT_ONFTRFRGTPSSWRD_06",
    "AMT_TSTNCTVSR_09",
    "AFN_TSTPPCLNT_04",
    "AMT_TSTMSSNGTKN_15",
    "AMT_TSTNVLDTKN_11",
    "FN_GNRTSTTTKN",
    "CLS_USER_08",
    "AMT_TSTXSTNGSRWTHTTHSSCT_02",
    "AMT_TSTSCCSS_02",
    "AMT_OTHSSCTCLLBCK",
    "CLS_TSTVRFYSR",
    "AMT_WRTTKN_05",
]


def convert_to_variants(entity_id: str) -> dict:
    """Convert an underscore-separated ID to all separator variants."""
    parts = entity_id.split("_")

    # Current format: AMT_RDTKN_03
    underscore = entity_id

    # Dot format: AMT.RDTKN.03
    dot = ".".join(parts)

    # Dash format: AMT-RDTKN-03
    dash = "-".join(parts)

    # Mixed format: AMT.RDTKN-03 (dot for type join, dash for suffix)
    # Type is first part, rest joined with dots, but numeric suffix with dash
    if len(parts) >= 2:
        type_prefix = parts[0]
        stem = parts[1]
        suffix = parts[2] if len(parts) > 2 else None
        if suffix:
            mixed = f"{type_prefix}.{stem}-{suffix}"
        else:
            mixed = f"{type_prefix}.{stem}"
    else:
        mixed = dot

    # Part B proposal: Remove type prefix from displayed ID
    # AMT_RDTKN_03 -> RDTKN.03 (type AMT is already on the row)
    if len(parts) >= 2:
        stem = parts[1]
        suffix = parts[2] if len(parts) > 2 else None
        if suffix:
            no_prefix_dot = f"{stem}.{suffix}"
        else:
            no_prefix_dot = stem
    else:
        no_prefix_dot = parts[0]

    return {
        "underscore": underscore,
        "dot": dot,
        "dash": dash,
        "mixed": mixed,
        "no_prefix_dot": no_prefix_dot,
    }


def count_tokens(text: str, encoding) -> int:
    """Count tokens for a string."""
    return len(encoding.encode(text))


def main():
    # Load tokenizers
    cl100k = tiktoken.get_encoding("cl100k_base")  # GPT-4, Claude
    o200k = tiktoken.get_encoding("o200k_base")    # GPT-4o

    print("=" * 90)
    print("ENTITY ID TOKENIZER TEST")
    print("=" * 90)
    print()

    # Results storage
    results = {variant: {"cl100k": 0, "o200k": 0} for variant in
               ["underscore", "dot", "dash", "mixed", "no_prefix_dot"]}

    print(f"{'Entity ID':<35} {'Variant':<15} {'cl100k':>8} {'o200k':>8}")
    print("-" * 70)

    for entity_id in SAMPLE_IDS:
        variants = convert_to_variants(entity_id)

        for variant_name, variant_text in variants.items():
            cl100k_count = count_tokens(variant_text, cl100k)
            o200k_count = count_tokens(variant_text, o200k)

            results[variant_name]["cl100k"] += cl100k_count
            results[variant_name]["o200k"] += o200k_count

            if variant_name == "underscore":
                print(f"{entity_id:<35} {variant_name:<15} {cl100k_count:>8} {o200k_count:>8}")
            else:
                print(f"{'':<35} {variant_name:<15} {cl100k_count:>8} {o200k_count:>8}")
        print()

    print("=" * 90)
    print("SUMMARY (20 entity IDs)")
    print("=" * 90)
    print(f"{'Variant':<20} {'cl100k Total':>15} {'o200k Total':>15} {'cl100k Avg':>12} {'o200k Avg':>12}")
    print("-" * 74)

    baseline_cl100k = results["underscore"]["cl100k"]
    baseline_o200k = results["underscore"]["o200k"]

    for variant_name in ["underscore", "dot", "dash", "mixed", "no_prefix_dot"]:
        cl100k_total = results[variant_name]["cl100k"]
        o200k_total = results[variant_name]["o200k"]
        cl100k_avg = cl100k_total / len(SAMPLE_IDS)
        o200k_avg = o200k_total / len(SAMPLE_IDS)

        cl100k_delta = cl100k_total - baseline_cl100k
        o200k_delta = o200k_total - baseline_o200k

        delta_str = ""
        if variant_name != "underscore":
            delta_str = f" ({cl100k_delta:+d}/{o200k_delta:+d})"

        print(f"{variant_name:<20} {cl100k_total:>15} {o200k_total:>15} {cl100k_avg:>12.2f} {o200k_avg:>12.2f}{delta_str}")

    print()
    print("Key findings:")
    print(f"- Underscore baseline: {baseline_cl100k} tokens (cl100k), {baseline_o200k} tokens (o200k)")

    best_variant = min(results.keys(), key=lambda k: results[k]["cl100k"])
    best_savings_cl100k = baseline_cl100k - results[best_variant]["cl100k"]
    best_savings_o200k = baseline_o200k - results[best_variant]["o200k"]

    print(f"- Best variant: {best_variant} (saves {best_savings_cl100k} cl100k, {best_savings_o200k} o200k tokens)")

    # Part B specific: no_prefix_dot savings
    no_prefix_savings_cl100k = baseline_cl100k - results["no_prefix_dot"]["cl100k"]
    no_prefix_savings_o200k = baseline_o200k - results["no_prefix_dot"]["o200k"]
    print(f"- Part B (no_prefix_dot): saves {no_prefix_savings_cl100k} cl100k, {no_prefix_savings_o200k} o200k tokens")
    print(f"  Per entity: {no_prefix_savings_cl100k/len(SAMPLE_IDS):.2f} cl100k, {no_prefix_savings_o200k/len(SAMPLE_IDS):.2f} o200k")


if __name__ == "__main__":
    main()
