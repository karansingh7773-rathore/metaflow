# `decompress_list` crashes with `ValueError` on multi-colon input paths — silent data corruption risk

## Summary

`metaflow.util.decompress_list()` uses bare tuple unpacking on `str.split(":")` at [line 395](https://github.com/Netflix/metaflow/blob/master/metaflow/util.py#L395):

```python
prefix, suffixes = decoded.split(rangedelim)
```

When the compressed string contains **more than one colon** (e.g., Airflow-style run-ids like `manual__2022-03-15T01:26:41`), this crashes with:

```
ValueError: too many values to unpack (expected 2)
```

An **empty string** input also crashes with `IndexError: string index out of range` at line 387.

This function is called from **every cloud orchestrator's task execution path**, making it a pipeline-crashing bug in production.

> **⚠️ Data Corruption Warning:** A naive fix of `split(rangedelim, 1)` is **worse than the crash** — it silently corrupts path data by splitting at the *first* colon instead of the *last*. The correct fix is `rsplit(rangedelim, 1)`.

## Environment

- **Python:** 3.12.3 (tested under WSL2 Ubuntu, also reproducible on any platform)
- **Metaflow:** current main branch
- **Affected orchestrators:** AWS Step Functions, Argo Workflows, Airflow (has workaround)

## Root Cause

`compress_list()` produces strings in the format `prefix:suffix1,suffix2,...` where `:` is the `rangedelim`. The prefix is the longest common prefix of the input paths. If the paths themselves contain colons, the prefix will also contain colons, producing a compressed string with **multiple colons**.

Example:
- Input paths: `["job:task:A", "job:task:B"]`
- Longest common prefix: `"job:task:"`
- Compressed string: `"job:task::A,B"` (prefix `"job:task:"` + rangedelim `":"` + suffixes `"A,B"`)

The current `split(":")` on `"job:task::A,B"` returns `["job", "task", "", "A,B"]` — 4 elements, tuple-unpacked into 2 variables → **ValueError**.

## Callers (Cloud Impact)

| Caller | File | Line | Context |
|--------|------|------|---------|
| `step` CLI command | `cli_components/step_cmd.py` | 150 | **Every cloud task execution** |
| `spin_step` CLI command | `cli_components/step_cmd.py` | 281 | Task debugging/replay |
| Argo conditional paths | `plugins/argo/conditional_input_paths.py` | 15 | Argo foreach/conditional joins |
| Step Functions | `plugins/aws/step_functions/step_functions.py` | 692 | Input path construction |

Airflow **explicitly works around** this bug by hashing run-ids to remove colons. From [`airflow_utils.py` line 163](https://github.com/Netflix/metaflow/blob/master/metaflow/plugins/airflow/airflow_utils.py#L163):

```python
# Airflow run_ids are of the form : "manual__2022-03-15T01:26:41.186781+00:00"
# Such run-ids break the `metaflow.util.decompress_list`
```

## Steps to Reproduce

```python
# reproduce_decompress_bug.py
from metaflow.util import decompress_list

# Bug 1: Multi-colon input → ValueError
try:
    result = decompress_list("job:task::A,B")
    print(f"Result: {result}")
except ValueError as e:
    print(f"CRASH: {e}")

# Bug 2: Empty string → IndexError  
try:
    result = decompress_list("")
    print(f"Result: {result}")
except IndexError as e:
    print(f"CRASH: {e}")
```

**Output:**
```
CRASH: too many values to unpack (expected 2)
CRASH: string index out of range
```

## Chaos Test Output (WSL Proof)

Full chaos test with 6 scenarios comparing ORIGINAL, WRONG FIX (`split(:,1)`), and CORRECT FIX (`rsplit(:,1)`):

```
======================================================================
CHAOS TEST: decompress_list Bug & Fix Validation
======================================================================

--- Test 1: Simple paths (no colons in prefix) ---
  Original:   ['run123/step_a/task1', 'run123/step_a/task2', 'run123/step_a/task3']
  Compressed: run123/step_a/task:1,2,3
  ✓ PASS ORIGINAL
  ✓ PASS WRONG FIX
  ✓ PASS CORRECT FIX

--- Test 2: Paths with colons in prefix (Airflow-style) ---
  Compressed: sfn-manual__2022-03-15T01:26:41:/step_a/task1,/step_b/task2
  Expected:   ['sfn-manual__2022-03-15T01:26:41/step_a/task1', 'sfn-manual__2022-03-15T01:26:41/step_b/task2']
  ✗ CRASH ORIGINAL: ValueError: too many values to unpack (expected 2)
  ✗ FAIL  WRONG FIX
         Expected: ['sfn-manual__2022-03-15T01:26:41/step_a/task1', ...]
         Got:      ['sfn-manual__2022-03-15T0126:41:/step_a/task1', ...]  ← DATA CORRUPTED
  ✓ PASS CORRECT FIX

--- Test 3: Empty string input ---
  ✗ CRASH ORIGINAL: IndexError: string index out of range
  ✓ PASS WRONG FIX
  ✓ PASS CORRECT FIX

--- Test 4: Step Functions foreach join (colon as delimiter) ---
  Compressed: sfn-abc123/train/:task_001,task_002,task_003
  ✓ PASS ORIGINAL
  ✓ PASS WRONG FIX
  ✓ PASS CORRECT FIX

--- Test 5: User's exact example — prefix with colons ---
  Compressed: job:task::A,B
  Expected:   ['job:task:A', 'job:task:B']
  ✗ CRASH ORIGINAL: ValueError: too many values to unpack (expected 2)
  ✗ FAIL  WRONG FIX
         Expected: ['job:task:A', 'job:task:B']
         Got:      ['jobtask::A', 'jobB']  ← DATA COMPLETELY DESTROYED
  ✓ PASS CORRECT FIX

--- Test 6: Single item, no colons ---
  ✓ PASS ORIGINAL
  ✓ PASS WRONG FIX
  ✓ PASS CORRECT FIX

======================================================================
CONCLUSION:
  - ORIGINAL:    Crashes (ValueError) on multi-colon inputs
  - WRONG FIX:   split(':', 1) silently CORRUPTS paths
  - CORRECT FIX: rsplit(':', 1) preserves all data correctly
======================================================================
```

## Proposed Fix

Two-line change in `decompress_list()`:

```diff
 def decompress_list(lststr, separator=",", rangedelim=":", zlibmarker="!"):
+    if not lststr:
+        return []
     if lststr[0] == zlibmarker:
         lstbytes = base64.b64decode(lststr[1:])
         decoded = zlib.decompress(zlib.decompress(lstbytes)).decode("utf-8")
@@ line 395
     if rangedelim in decoded:
-        prefix, suffixes = decoded.split(rangedelim)
+        prefix, suffixes = decoded.rsplit(rangedelim, 1)
         return [prefix + suffix for suffix in suffixes.split(separator)]
```

**Why `rsplit` and not `split`:**

`compress_list()` builds strings as `<prefix><rangedelim><suffixes>`. The delimiter is always the **last** colon in the string, placed between the longest common prefix and the comma-separated suffixes. `rsplit(":", 1)` splits from the right, correctly isolating the prefix (which may contain colons) from the suffixes.

## Impact

- **Severity:** High — crashes any cloud pipeline where pathspecs contain colons
- **Risk of fix:** Minimal — `rsplit` is a strict superset of current behavior for single-colon inputs
- **Test coverage:** Currently **zero** — `compress_list`/`decompress_list` have no unit tests
- **Known workaround:** Airflow hashes run-ids to avoid colons; other orchestrators have no workaround

## Next Steps — I'd Like to Fix This

I've already completed the full analysis, root cause trace, and chaos testing for this bug. I'll be opening a PR shortly with:

1. The two-line `rsplit` fix + empty-string guard in `metaflow/util.py`
2. Comprehensive unit tests for `compress_list`/`decompress_list` round-trips (currently at **zero** test coverage)
3. The chaos reproduction script as evidence

**PR will be submitted within 24 hours of this issue being acknowledged.** Happy to adjust the approach based on maintainer feedback before merging.
