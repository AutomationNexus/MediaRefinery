#!/usr/bin/env python3
"""
Check test standards compliance.

This script enforces the documented test standards:
- Maximum 3 mocks per test (or use composite fixtures)
- No deep mock chains
- File size limits (500 lines)
- Using builders instead of manual construction

Usage:
    python tests/check_test_standards.py
    python tests/check_test_standards.py --fix  # Attempts automatic fixes (where safe)
"""

import argparse
import re
from pathlib import Path


def find_deep_mock_chains(content: str, filepath: Path) -> list[tuple[int, str]]:
    """Find deep mock chains like .cpu.return_value.item.return_value

    Note: train_loss tensor chains are acceptable (PyTorch tensors need .cpu().item()).
    We only flag checkpoint score chains which should use FakeCheckpoint.
    """
    violations = []
    # Pattern for checkpoint score chains (should use FakeCheckpoint)
    checkpoint_pattern = (
        r"(mock_checkpoint|mock_score|score_mock|checkpoint.*best_model_score)"
        r"\.cpu\.return_value\.item\.return_value"
    )

    for line_num, line in enumerate(content.split("\n"), 1):
        if re.search(checkpoint_pattern, line, re.IGNORECASE):
            violations.append((line_num, line.strip()))

    return violations


def count_patch_decorators(content: str) -> list[tuple[str, int]]:
    """Count @patch decorators per test function."""
    violations = []

    # Find all test functions and count their @patch decorators
    lines = content.split("\n")
    current_test = None
    patch_count = 0

    for _, line in enumerate(lines):
        # Check if this is a test function definition
        if re.match(r"^\s+def test_", line):
            if current_test and patch_count > 3:
                violations.append((current_test, patch_count))
            # Extract test name
            match = re.match(r"^\s+def (test_\w+)", line)
            if match:
                current_test = match.group(1)
                patch_count = 0
        # Count @patch decorators
        elif current_test and re.match(r"^\s+@patch", line):
            patch_count += 1
        # Reset on blank line or non-decorator non-def line
        elif current_test and line.strip() and not line.strip().startswith("@"):
            if patch_count > 3:
                violations.append((current_test, patch_count))
            current_test = None
            patch_count = 0

    return violations


def check_file_size(filepath: Path) -> bool:
    """Check if file exceeds 500 lines."""
    with open(filepath) as f:
        return len(f.readlines()) > 500


def check_manual_dataframe_construction(content: str, filepath: Path) -> list[int]:
    """Find manual DataFrame constructions (pd.DataFrame({...}))."""
    violations = []

    # Pattern to find pd.DataFrame({...}) constructions
    # Look for patterns that suggest manual construction rather than using TimeSeriesDataBuilder
    pattern = r"pd\.DataFrame\(\s*\{"

    # Get all lines to analyze context
    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        if re.search(pattern, line):
            # Skip if TimeSeriesDataBuilder is imported or used in this file
            if "TimeSeriesDataBuilder" in content or "from tests.builders" in content:
                continue

            # Count how many continuation lines and field lines there are
            continuation_count = 0
            field_count = 0
            check_line = line_num
            inside_dict = False

            while check_line < len(lines) and check_line < line_num + 20:
                current_line = lines[check_line]

                if "{" in current_line and not inside_dict:
                    inside_dict = True

                # Count field definitions (lines with ':' that are actual field definitions)
                if inside_dict and ":" in current_line and not current_line.strip().startswith("#"):
                    # Check if it looks like a key-value pair
                    if re.search(r'[\'"a-zA-Z_]\w*[\'"]?\s*:', current_line) or re.search(
                        r"[a-zA-Z_]\w*\s*:", current_line
                    ):
                        field_count += 1

                if "}" in current_line:
                    break

                if inside_dict and "}" not in current_line and check_line > line_num:
                    continuation_count += 1

                check_line += 1

            # Only flag if it's a time series construction (many rows or many fields = complex)
            # Small fixture DataFrames with few fields are okay
            # Flag if has many fields (>=5) or is in a time series context
            if field_count >= 5:
                violations.append(line_num)

    return violations


# Existing test files that predate this check and exceed the 500-line limit.
# New test files must stay under 500 lines.
_SIZE_SKIP = {
    "test_actions.py",
    "test_edge_cases.py",
    "test_immich.py",
    "test_scan_privacy.py",
    "service/test_edge_cases.py",
    "service/test_model_lifecycle.py",
    "service/test_real_runner.py",
}


def check_test_standards(test_dir: Path = None, fix: bool = False) -> int:
    """Check all test files for standards compliance."""
    if test_dir is None:
        test_dir = Path(__file__).parent

    violations_found = 0

    # Find all test files
    test_files = list(test_dir.rglob("test_*.py"))

    print(f"Checking {len(test_files)} test files...\n")

    for test_file in test_files:
        file_violations = []

        try:
            content = test_file.read_text()
        except Exception as e:
            print(f"ERROR: Could not read {test_file}: {e}")
            violations_found += 1
            continue

        # Check file size (skip grandfathered large files)
        rel = str(test_file.relative_to(test_dir)).replace("\\", "/")
        if rel not in _SIZE_SKIP and check_file_size(test_file):
            line_count = len(content.split("\n"))
            file_violations.append(f"  [X] File size: {line_count} lines (limit: 500)")

        # Check for deep mock chains
        deep_chains = find_deep_mock_chains(content, test_file)
        if deep_chains:
            file_violations.append(f"  [X] Deep mock chains: {len(deep_chains)} found")
            for line_num, line in deep_chains[:3]:  # Show first 3
                file_violations.append(f"      Line {line_num}: {line[:80]}...")
            if len(deep_chains) > 3:
                file_violations.append(f"      ... and {len(deep_chains) - 3} more")

        # Check for excessive @patch decorators
        excessive_patches = count_patch_decorators(content)
        if excessive_patches:
            file_violations.append(
                f"  [X] Tests with >3 @patch decorators: {len(excessive_patches)}"
            )
            for test_name, count in excessive_patches[:3]:  # Show first 3
                file_violations.append(f"      {test_name}: {count} decorators")
            if len(excessive_patches) > 3:
                file_violations.append(f"      ... and {len(excessive_patches) - 3} more")

        # Check for manual DataFrame construction
        manual_dfs = check_manual_dataframe_construction(content, test_file)
        if manual_dfs and "TimeSeriesDataBuilder" not in content:
            file_violations.append(
                f"  [W] Manual DataFrame construction: {len(manual_dfs)} found"
                " (consider TimeSeriesDataBuilder)"
            )
            for line_num in manual_dfs[:3]:  # Show first 3
                file_violations.append(f"      Line {line_num}")
            if len(manual_dfs) > 3:
                file_violations.append(f"      ... and {len(manual_dfs) - 3} more")

        if file_violations:
            print(f"File: {test_file.relative_to(test_dir)}")
            for violation in file_violations:
                print(violation)
            print()
            violations_found += 1

    return violations_found


def main():
    parser = argparse.ArgumentParser(description="Check test standards compliance")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Attempt automatic fixes (not implemented yet)",
    )
    parser.add_argument(
        "--test-dir", type=Path, help="Directory containing tests (default: tests/)"
    )
    args = parser.parse_args()

    violations = check_test_standards(args.test_dir, fix=args.fix)

    if violations == 0:
        print("All test files comply with standards!")
        return 0
    else:
        print(f"\nFound violations in {violations} file(s)")
        return 1


if __name__ == "__main__":
    exit(main())
