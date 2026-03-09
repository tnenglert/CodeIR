#!/usr/bin/env python3
"""Generate Aider repo maps at different token budgets for benchmark comparison.

This script uses Aider's internal RepoMap class to generate repo maps
that can be compared against SemanticIR's compression output.
"""

import os
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPO_PATH = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"
OUTPUT_DIR = ROOT / "tests" / "eval" / "baselines" / "aider"


def get_all_python_files(repo_path: Path) -> list[str]:
    """Get all Python files in the repository."""
    files = []
    for f in repo_path.rglob("*.py"):
        # Skip hidden directories and common non-source dirs
        parts = f.relative_to(repo_path).parts
        if any(p.startswith('.') or p in ('__pycache__', 'node_modules', '.git') for p in parts):
            continue
        files.append(str(f.relative_to(repo_path)))
    return sorted(files)


def generate_repo_map(repo_path: Path, map_tokens: int) -> str:
    """Generate Aider repo map at specified token budget."""
    try:
        from aider.repomap import RepoMap
        from aider.models import Model
        from aider.io import InputOutput
    except ImportError as e:
        print(f"Error importing aider modules: {e}")
        print("Make sure aider-chat is installed: pip install aider-chat")
        sys.exit(1)

    # Change to repo directory
    original_cwd = os.getcwd()
    os.chdir(repo_path)

    try:
        # Get list of all Python files
        all_files = get_all_python_files(repo_path)
        print(f"Found {len(all_files)} Python files")

        # Create a minimal IO object
        io = InputOutput(yes=True)

        # Create model for tokenizer (we're not actually calling the model)
        # Use gpt-4.1 as specified
        model = Model("gpt-4.1")

        # Create RepoMap instance
        rm = RepoMap(
            map_tokens=map_tokens,
            root=str(repo_path),
            main_model=model,
            io=io,
        )

        # Generate the map
        # RepoMap.get_repo_map(chat_files, other_files)
        # chat_files are files currently in context
        # other_files are all other files to potentially include
        repo_map = rm.get_repo_map([], all_files)

        return repo_map or ""

    finally:
        os.chdir(original_cwd)


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken (same as SemanticIR benchmarks)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        # Rough estimate: ~4 chars per token
        return len(text) // 4


def main():
    print(f"Repository: {REPO_PATH}")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    if not REPO_PATH.exists():
        print(f"Error: Repository not found at {REPO_PATH}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate maps at different token budgets
    budgets = [1024, 2048, 4096]

    for budget in budgets:
        print(f"Generating map at {budget} token budget...")

        try:
            repo_map = generate_repo_map(REPO_PATH, budget)

            if not repo_map:
                print(f"  Warning: Empty map generated for {budget} tokens")
                continue

            # Count actual tokens
            actual_tokens = count_tokens(repo_map)

            # Save to file
            suffix = f"{budget // 1024}k"
            output_path = OUTPUT_DIR / f"aider_map_{suffix}.txt"
            output_path.write_text(repo_map, encoding="utf-8")

            print(f"  Saved to: {output_path}")
            print(f"  Actual tokens: {actual_tokens}")
            print(f"  Lines: {len(repo_map.splitlines())}")
            print()

        except Exception as e:
            print(f"  Error generating map: {e}")
            import traceback
            traceback.print_exc()
            continue

    print("Done!")


if __name__ == "__main__":
    main()
