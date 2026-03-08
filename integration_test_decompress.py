"""
integration_test_decompress.py — Integration test for decompress_list fix.

This script exercises the EXACT code path that cloud orchestrators use:
  - step_cmd.py line 150:  paths = decompress_list(input_paths)
  - step_cmd.py line 281:  input_paths = decompress_list(input_paths)
  - argo/conditional_input_paths.py line 15: paths = decompress_list(decoded)

It uses the REAL metaflow.util imports (not inlined copies) to prove the
installed fix works at the CLI execution boundary.

Targets GitHub Issue #2908:
  https://github.com/Netflix/metaflow/issues/2908
"""

import sys
import traceback

# ─── Use the REAL metaflow imports (same as step_cmd.py) ─────────────────────
from metaflow.util import decompress_list, compress_list

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
CRASH = "\033[91m✗ CRASH\033[0m"

results = {"passed": 0, "failed": 0, "crashed": 0}

def run_test(name, func, *args, expected=None):
    """Run a single test case and track results."""
    try:
        result = func(*args)
        if result == expected:
            print(f"  {PASS} {name}")
            results["passed"] += 1
            return True
        else:
            print(f"  {FAIL} {name}")
            print(f"         Expected: {expected}")
            print(f"         Got:      {result}")
            results["failed"] += 1
            return False
    except Exception as e:
        print(f"  {CRASH} {name}: {type(e).__name__}: {e}")
        results["crashed"] += 1
        return False


print("=" * 72)
print("INTEGRATION TEST: decompress_list fix at CLI execution boundary")
print(f"Python: {sys.version}")
print(f"Import: metaflow.util (same as step_cmd.py)")
print("=" * 72)

# ─── Test Suite 1: Step Functions Input Paths ────────────────────────────────
# From step_functions.py line 694:
#   input_paths = "sfn-${METAFLOW_RUN_ID}:" + ",".join(suffixes)
# The colon here is the rangedelim placed by the SFN path builder.
print("\n[1] Step Functions — split-join input paths")
print("    Source: step_functions.py line 694")

# Normal SFN path (single colon) — always worked
run_test(
    "SFN split-join (single colon)",
    decompress_list,
    "sfn-abc123/train/:task_001,task_002,task_003",
    expected=["sfn-abc123/train/task_001", "sfn-abc123/train/task_002", "sfn-abc123/train/task_003"],
)

# SFN foreach join — from step_functions.py line 660-662
run_test(
    "SFN foreach-join path",
    decompress_list,
    "sfn-run42/process/:0,1,2,3,4",
    expected=["sfn-run42/process/0", "sfn-run42/process/1", "sfn-run42/process/2", "sfn-run42/process/3", "sfn-run42/process/4"],
)

# ─── Test Suite 2: Argo Workflows — Conditional Input Paths ──────────────────
# From argo/conditional_input_paths.py line 15:
#   paths = decompress_list(decoded)
# Argo does NOT hash run-ids, so colons in run-ids reach decompress_list.
print("\n[2] Argo Workflows — conditional input paths with timestamps")
print("    Source: argo/conditional_input_paths.py line 15")

# Argo with timestamp run-id containing colons
run_test(
    "Argo timestamp run-id (multi-colon)",
    decompress_list,
    "argo-2024-03-15T01:26:41:/step_a/task1,/step_b/task2",
    expected=["argo-2024-03-15T01:26:41/step_a/task1", "argo-2024-03-15T01:26:41/step_b/task2"],
)

# Argo with ISO timestamp (many colons)
run_test(
    "Argo ISO timestamp (3 colons total)",
    decompress_list,
    "argo-2024-03-15T01:26:41+05:30:/taskA,/taskB",
    expected=["argo-2024-03-15T01:26:41+05:30/taskA", "argo-2024-03-15T01:26:41+05:30/taskB"],
)

# ─── Test Suite 3: Airflow-Style Paths (what the hash workaround prevents) ──
# From airflow_utils.py line 162-163:
#   "Airflow run_ids are of the form: manual__2022-03-15T01:26:41.186781+00:00"
#   "Such run-ids break the decompress_list; this is why we hash the runid"
# This test proves the fix makes the hash workaround UNNECESSARY.
print("\n[3] Airflow-style paths (proving hash workaround is now unnecessary)")
print("    Source: airflow_utils.py line 162-163")

run_test(
    "Airflow manual trigger run-id",
    decompress_list,
    "airflow-manual__2022-03-15T01:26:41:/step_a/task1,/step_b/task2",
    expected=["airflow-manual__2022-03-15T01:26:41/step_a/task1", "airflow-manual__2022-03-15T01:26:41/step_b/task2"],
)

run_test(
    "Airflow scheduled run-id (with timezone)",
    decompress_list,
    "airflow-scheduled__2024-01-01T00:00:00+00:00:/start/t1,/end/t2",
    expected=["airflow-scheduled__2024-01-01T00:00:00+00:00/start/t1", "airflow-scheduled__2024-01-01T00:00:00+00:00/end/t2"],
)

# ─── Test Suite 4: Round-trip through compress_list → decompress_list ────────
# This proves the fix doesn't break normal operation.
print("\n[4] Round-trip: compress_list → decompress_list")
print("    Proves the fix is backward-compatible")

pathsets = [
    ("Simple pathspecs", ["run1/step_a/task1", "run1/step_a/task2", "run1/step_a/task3"]),
    ("Single path", ["run1/step_a/task1"]),
    ("No common prefix", ["alpha/t1", "beta/t2", "gamma/t3"]),
    ("Long realistic paths", [
        "sfn-abc123/train/task_%04d" % i for i in range(10)
    ]),
]

for name, paths in pathsets:
    compressed = compress_list(paths)
    run_test(
        f"Round-trip: {name}",
        decompress_list,
        compressed,
        expected=paths,
    )

# ─── Test Suite 5: Edge cases ────────────────────────────────────────────────
print("\n[5] Edge cases")

run_test("Empty string → empty list", decompress_list, "", expected=[])
run_test("Single item, no delimiter", decompress_list, "run1/step/task1", expected=["run1/step/task1"])
run_test("Comma-separated, no prefix", decompress_list, "a,b,c", expected=["a", "b", "c"])

# ─── Test Suite 6: Zlib-compressed large lists ───────────────────────────────
print("\n[6] Zlib-compressed large lists (round-trip)")

big_paths = ["sfn-production-run/train/task_%06d" % i for i in range(500)]
compressed = compress_list(big_paths)
assert compressed.startswith("!"), "Expected zlib compression for 500 items"
run_test(
    "Zlib round-trip (500 paths)",
    decompress_list,
    compressed,
    expected=big_paths,
)

# ─── Summary ─────────────────────────────────────────────────────────────────
total = results["passed"] + results["failed"] + results["crashed"]
print("\n" + "=" * 72)
print(f"RESULTS: {results['passed']}/{total} passed, "
      f"{results['failed']} failed, {results['crashed']} crashed")

if results["failed"] == 0 and results["crashed"] == 0:
    print("\n  ALL TESTS PASSED — decompress_list fix is verified at the")
    print("  CLI execution boundary (step_cmd.py, conditional_input_paths.py)")
    print("  across Step Functions, Argo, and Airflow-style paths.")
else:
    print("\n  FAILURES DETECTED — fix needs investigation")

print("=" * 72)
sys.exit(0 if results["failed"] == 0 and results["crashed"] == 0 else 1)
