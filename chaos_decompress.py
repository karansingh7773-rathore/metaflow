"""
chaos_decompress.py — Prove that decompress_list is broken and validate the fix.

Bug: metaflow/util.py line 395 uses bare tuple unpacking:
    prefix, suffixes = decoded.split(rangedelim)
This crashes with ValueError when the compressed string contains multiple colons.

Wrong fix: split(rangedelim, 1)  — splits at the FIRST colon, corrupting data.
Right fix: rsplit(rangedelim, 1) — splits at the LAST colon, preserving data.
"""

import base64
import zlib
import sys
import os

# ─── Inline the functions so we can test without modifying source ───────────

def compress_list(lst, separator=",", rangedelim=":", zlibmarker="!", zlibmin=500):
    """Exact copy from metaflow/util.py"""
    bad_items = [x for x in lst if separator in x or rangedelim in x or zlibmarker in x]
    if bad_items:
        raise ValueError(
            "Item '%s' includes a delimiter character "
            "so it can't be compressed" % bad_items[0]
        )
    from itertools import takewhile
    def longest_common_prefix(lst):
        if lst:
            return "".join(
                a for a, _ in takewhile(lambda t: t[0] == t[1], zip(min(lst), max(lst)))
            )
        return ""

    lcp = longest_common_prefix(lst)
    if len(lst) < 2 or not lcp:
        res = separator.join(lst)
    else:
        lcplen = len(lcp)
        residuals = [e[lcplen:] for e in lst]
        res = rangedelim.join((lcp, separator.join(residuals)))
    if len(res) < zlibmin:
        return res
    else:
        compressed = zlib.compress(zlib.compress(res.encode("utf-8")))
        return zlibmarker + base64.b64encode(compressed).decode("utf-8")


def decompress_ORIGINAL(lststr, separator=",", rangedelim=":", zlibmarker="!"):
    """ORIGINAL broken code from metaflow/util.py"""
    if lststr[0] == zlibmarker:
        lstbytes = base64.b64decode(lststr[1:])
        decoded = zlib.decompress(zlib.decompress(lstbytes)).decode("utf-8")
    else:
        decoded = lststr

    if rangedelim in decoded:
        prefix, suffixes = decoded.split(rangedelim)  # BUG: ValueError if >1 colon
        return [prefix + suffix for suffix in suffixes.split(separator)]
    else:
        return decoded.split(separator)


def decompress_WRONG_FIX(lststr, separator=",", rangedelim=":", zlibmarker="!"):
    """WRONG fix: split(rangedelim, 1) — splits at FIRST colon, CORRUPTS DATA"""
    if not lststr:
        return []
    if lststr[0] == zlibmarker:
        lstbytes = base64.b64decode(lststr[1:])
        decoded = zlib.decompress(zlib.decompress(lstbytes)).decode("utf-8")
    else:
        decoded = lststr

    if rangedelim in decoded:
        prefix, suffixes = decoded.split(rangedelim, 1)  # WRONG: splits at first ":"
        return [prefix + suffix for suffix in suffixes.split(separator)]
    else:
        return decoded.split(separator)


def decompress_CORRECT_FIX(lststr, separator=",", rangedelim=":", zlibmarker="!"):
    """CORRECT fix: rsplit(rangedelim, 1) — splits at LAST colon, preserves data"""
    if not lststr:
        return []
    if lststr[0] == zlibmarker:
        lstbytes = base64.b64decode(lststr[1:])
        decoded = zlib.decompress(zlib.decompress(lstbytes)).decode("utf-8")
    else:
        decoded = lststr

    if rangedelim in decoded:
        prefix, suffixes = decoded.rsplit(rangedelim, 1)  # CORRECT: splits at last ":"
        return [prefix + suffix for suffix in suffixes.split(separator)]
    else:
        return decoded.split(separator)


# ─── Test Cases ─────────────────────────────────────────────────────────────

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
CRASH = "\033[91m✗ CRASH\033[0m"

def test(name, func, input_str, expected):
    try:
        result = func(input_str)
        if result == expected:
            print(f"  {PASS} {name}: {result}")
            return True
        else:
            print(f"  {FAIL} {name}")
            print(f"         Expected: {expected}")
            print(f"         Got:      {result}")
            return False
    except Exception as e:
        print(f"  {CRASH} {name}: {type(e).__name__}: {e}")
        return False


print("=" * 70)
print("CHAOS TEST: decompress_list Bug & Fix Validation")
print("=" * 70)

# ─── Test 1: Simple paths (no colons in prefix) ────────────────────────────
print("\n--- Test 1: Simple paths (no colons in prefix) ---")
# These work for all versions since the prefix has no colons
simple_paths = ["run123/step_a/task1", "run123/step_a/task2", "run123/step_a/task3"]
compressed = compress_list(simple_paths)
print(f"  Original:   {simple_paths}")
print(f"  Compressed: {compressed}")

test("ORIGINAL",     decompress_ORIGINAL,     compressed, simple_paths)
test("WRONG FIX",    decompress_WRONG_FIX,    compressed, simple_paths)
test("CORRECT FIX",  decompress_CORRECT_FIX,  compressed, simple_paths)

# ─── Test 2: Airflow-style paths WITH colons (the real bug) ────────────────
print("\n--- Test 2: Paths with colons in prefix (Airflow-style) ---")
# compress_list rejects items containing ":" so we simulate what
# the Step Functions / Argo path builder does MANUALLY.
# From step_functions.py line 694:
#   input_paths = "sfn-${METAFLOW_RUN_ID}:" + ",".join(suffixes)
# If RUN_ID contains colons (like Airflow's "manual__2022-03-15T01:26:41"),
# the compressed string has multiple colons.
# NOTE: The LAST colon is the rangedelim. rsplit(":", 1) correctly splits there.
#   prefix = "sfn-manual__2022-03-15T01:26:41"
#   suffixes = "/step_a/task1,/step_b/task2"
multi_colon_str = "sfn-manual__2022-03-15T01:26:41:/step_a/task1,/step_b/task2"
expected = [
    "sfn-manual__2022-03-15T01:26:41/step_a/task1",
    "sfn-manual__2022-03-15T01:26:41/step_b/task2",
]
print(f"  Compressed: {multi_colon_str}")
print(f"  Expected:   {expected}")

test("ORIGINAL",     decompress_ORIGINAL,     multi_colon_str, expected)
test("WRONG FIX",    decompress_WRONG_FIX,    multi_colon_str, expected)
test("CORRECT FIX",  decompress_CORRECT_FIX,  multi_colon_str, expected)

# ─── Test 3: Empty string (IndexError) ────────────────────────────────────
print("\n--- Test 3: Empty string input ---")
test("ORIGINAL",     decompress_ORIGINAL,     "", [])
test("WRONG FIX",    decompress_WRONG_FIX,    "", [])
test("CORRECT FIX",  decompress_CORRECT_FIX,  "", [])

# ─── Test 4: Step Functions foreach join path ──────────────────────────────
print("\n--- Test 4: Step Functions foreach join (colon as delimiter) ---")
# From step_functions.py line 660-662:
#   input_paths = "sfn-${METAFLOW_RUN_ID}/%s/:${METAFLOW_PARENT_TASK_IDS}" % node.in_funcs[0]
# After env var substitution, this might look like:
foreach_str = "sfn-abc123/train/:task_001,task_002,task_003"
expected_foreach = [
    "sfn-abc123/train/task_001",
    "sfn-abc123/train/task_002",
    "sfn-abc123/train/task_003",
]
print(f"  Compressed: {foreach_str}")
print(f"  Expected:   {expected_foreach}")

test("ORIGINAL",     decompress_ORIGINAL,     foreach_str, expected_foreach)
test("WRONG FIX",    decompress_WRONG_FIX,    foreach_str, expected_foreach)
test("CORRECT FIX",  decompress_CORRECT_FIX,  foreach_str, expected_foreach)

# ─── Test 5: The user's exact example (colons in every path) ──────────────
print("\n--- Test 5: User's exact example — prefix with colons ---")
# Manually constructed: prefix = "job:task:", suffixes = "A,B"
# Compressed string = "job:task::A,B"
user_example = "job:task::A,B"
expected_user = ["job:task:A", "job:task:B"]
print(f"  Compressed: {user_example}")
print(f"  Expected:   {expected_user}")

test("ORIGINAL",     decompress_ORIGINAL,     user_example, expected_user)
test("WRONG FIX",    decompress_WRONG_FIX,    user_example, expected_user)
test("CORRECT FIX",  decompress_CORRECT_FIX,  user_example, expected_user)

# ─── Test 6: Single item, no colon ────────────────────────────────────────
print("\n--- Test 6: Single item, no colons ---")
single = "run123/step_a/task1"
test("ORIGINAL",     decompress_ORIGINAL,     single, [single])
test("WRONG FIX",    decompress_WRONG_FIX,    single, [single])
test("CORRECT FIX",  decompress_CORRECT_FIX,  single, [single])

# ─── Summary ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("CONCLUSION:")
print("  - ORIGINAL:    Crashes (ValueError) on multi-colon inputs")
print("  - WRONG FIX:   split(':', 1) silently CORRUPTS paths")  
print("  - CORRECT FIX: rsplit(':', 1) preserves all data correctly")
print("=" * 70)
