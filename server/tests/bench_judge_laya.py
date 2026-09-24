#!/usr/bin/env python3
"""
Benchmark Laya as a chat auto-router judge.

Tests Laya's ability to classify chat prompts into the real judge categories
used by /api/chat's auto-router (_JUDGE_CATEGORIES from server/main.py).

NOT collected by pytest (guarded by if __name__ == '__main__').

Metrics:
- Overall accuracy
- Per-category accuracy
- Mean confidence for correct vs incorrect predictions
- Median latency per classification
- Confusion matrix
"""

import ast
import json
import re
import statistics
import sys
import time
from pathlib import Path


def extract_judge_categories_from_main() -> tuple[list[str], str]:
    """
    Extract _JUDGE_CATEGORIES and _JUDGE_PROMPT from server/main.py using AST.

    Returns:
        (list of category names, judge prompt text) or raises ValueError
    """
    main_path = Path(__file__).parent.parent / "main.py"
    with open(main_path) as f:
        content = f.read()

    tree = ast.parse(content)

    categories = None
    prompt = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "_JUDGE_CATEGORIES":
                        if isinstance(node.value, ast.List):
                            categories = [
                                elt.value
                                for elt in node.value.elts
                                if isinstance(elt, ast.Constant)
                            ]
                    elif target.id == "_JUDGE_PROMPT":
                        if isinstance(node.value, ast.Constant):
                            prompt = node.value.value

    if categories is None:
        raise ValueError("Could not find _JUDGE_CATEGORIES in main.py")
    if prompt is None:
        raise ValueError("Could not find _JUDGE_PROMPT in main.py")

    return categories, prompt


def main():
    """Run Laya benchmark on chat prompts."""
    print("=" * 80)
    print("LAYA CHAT AUTO-ROUTER JUDGE BENCHMARK")
    print("=" * 80)
    print()

    # Extract judge categories
    try:
        categories, judge_prompt = extract_judge_categories_from_main()
        print(f"Extracted {len(categories)} judge categories from server/main.py:")
        for cat in categories:
            print(f"  - {cat}")
        print()
    except Exception as exc:
        print(f"Error extracting categories: {exc}")
        sys.exit(1)

    # Load sample data
    sample_file = Path(__file__).parent / "bench_judge_laya_sample.json"
    if not sample_file.exists():
        print(f"Error: {sample_file} not found")
        sys.exit(1)

    with open(sample_file) as f:
        data = json.load(f)

    samples = data.get("samples", [])
    if not samples:
        print("Error: No samples found in JSON file")
        sys.exit(1)

    # Import Laya lazily
    try:
        import laya_decider as _laya
    except ImportError:
        print("Error: laya_decider module not available")
        print("Install with: pip install laya")
        sys.exit(1)

    # Check if Laya is available
    if not _laya.is_available():
        print("Error: Laya model not available")
        print("Run: python3 server/download_models.py")
        sys.exit(1)

    # Ensure model is loaded
    if not _laya.is_available():
        print("Downloading Laya model...")
        if not _laya.ensure_downloaded():
            print("Failed to download Laya model")
            sys.exit(1)

    print(f"Running benchmark on {len(samples)} samples...\n")

    # Track results
    results = {
        "correct": {"confidences": [], "latencies": []},
        "incorrect": {"confidences": [], "latencies": []},
        "by_category": {},
        "confusion_matrix": {},  # category -> {predicted -> count}
    }

    # Build criteria dict from categories with descriptions
    criteria = {cat: f"{cat} category" for cat in categories}

    # Run each sample through Laya judge
    for i, sample in enumerate(samples, 1):
        expected_category = sample["category"]
        text = sample["text"]

        # Ensure category tracking exists
        if expected_category not in results["by_category"]:
            results["by_category"][expected_category] = {
                "total": 0,
                "correct": 0,
                "confidences": [],
                "latencies": [],
            }
        if expected_category not in results["confusion_matrix"]:
            results["confusion_matrix"][expected_category] = {}

        # Measure latency
        start = time.time()
        prediction = _laya.judge_choice(
            text,
            question_name="category",
            criteria=criteria,
            instructions="Classify this chat prompt into one category.",
        )
        elapsed = time.time() - start

        results["by_category"][expected_category]["total"] += 1
        results["by_category"][expected_category]["latencies"].append(elapsed)

        if prediction is None:
            print(
                f"Sample {i:2d}: {expected_category:15s} -> ERROR (no prediction)"
            )
            continue

        predicted_category = prediction.get("choice")
        confidence = prediction.get("confidence", 0)

        is_correct = predicted_category == expected_category
        if is_correct:
            results["correct"]["confidences"].append(confidence)
            results["by_category"][expected_category]["correct"] += 1
        else:
            results["incorrect"]["confidences"].append(confidence)

        results["correct"]["latencies"].append(elapsed)

        # Update confusion matrix
        if predicted_category not in results["confusion_matrix"][expected_category]:
            results["confusion_matrix"][expected_category][predicted_category] = 0
        results["confusion_matrix"][expected_category][predicted_category] += 1

        status = "✓" if is_correct else "✗"
        print(
            f"Sample {i:2d}: {expected_category:15s} -> {predicted_category:15s} "
            f"[conf={confidence:.2f}, {elapsed*1000:.1f}ms] {status}"
        )

    # Print summary
    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS")
    print("=" * 80)

    total = len(samples)
    correct = len(results["correct"]["confidences"]) + len([
        1 for s in samples if
        _laya.judge_choice(
            s["text"],
            question_name="category",
            criteria=criteria,
            instructions="Classify this chat prompt into one category.",
        ) and _laya.judge_choice(
            s["text"],
            question_name="category",
            criteria=criteria,
            instructions="Classify this chat prompt into one category.",
        ).get("choice") == s["category"]
    ])

    print(f"\nOverall Accuracy: {len(results['correct']['confidences'])}/{total} "
          f"({100*len(results['correct']['confidences'])/total:.1f}%)")

    if results["correct"]["confidences"]:
        mean_conf_correct = statistics.mean(results["correct"]["confidences"])
        print(f"Mean confidence (correct): {mean_conf_correct:.3f}")

    if results["incorrect"]["confidences"]:
        mean_conf_incorrect = statistics.mean(results["incorrect"]["confidences"])
        print(f"Mean confidence (incorrect): {mean_conf_incorrect:.3f}")

    if results["correct"]["latencies"]:
        median_latency = statistics.median(results["correct"]["latencies"])
        print(f"Median latency (correct): {median_latency*1000:.1f}ms")

    print("\nPer-Category Breakdown:")
    print("-" * 80)
    for category in sorted(results["by_category"].keys()):
        cat_results = results["by_category"][category]
        total_cat = cat_results["total"]
        correct_cat = cat_results["correct"]
        acc = 100 * correct_cat / total_cat if total_cat > 0 else 0
        latencies = cat_results.get("latencies", [])
        median_latency_cat = (
            statistics.median(latencies) * 1000 if latencies else 0
        )
        print(
            f"  {category:20s}: {correct_cat}/{total_cat} "
            f"({acc:5.1f}%) - {median_latency_cat:6.1f}ms median"
        )

    print("\nConfusion Matrix:")
    print("-" * 80)
    print(f"{'Expected':20s} -> {'Predicted':20s} : Count")
    for expected in sorted(results["confusion_matrix"].keys()):
        for predicted in sorted(results["confusion_matrix"][expected].keys()):
            count = results["confusion_matrix"][expected][predicted]
            if predicted == expected:
                marker = "✓"
            else:
                marker = "✗"
            print(
                f"{expected:20s} -> {predicted:20s} : {count:3d} {marker}"
            )

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
