# fix: `decompress_list` crashes on multi-colon input paths

Fixes #2908

## Summary

`decompress_list()` in `metaflow/util.py` uses bare `split(rangedelim)` to split a compressed path string into `(prefix, suffixes)`. When the prefix itself contains colons (e.g., Airflow-style run-ids like `manual__2022-03-15T01:26:41`), this crashes with `ValueError: too many values to unpack`.

This function is called from **every cloud orchestrator's task execution path**: `step_cmd.py`, `argo/conditional_input_paths.py`, and Step Functions. Airflow explicitly works around this by [hashing run-ids to remove colons](https://github.com/Netflix/metaflow/blob/master/metaflow/plugins/airflow/airflow_utils.py#L163).

## Changes

### `metaflow/util.py`
- **Empty-string guard**: Return `[]` instead of `IndexError` when `lststr` is empty
- **`rsplit(rangedelim, 1)`**: Split at the **last** colon (the actual delimiter placed by `compress_list`) instead of all colons

> **Why `rsplit` and not `split`?** `compress_list` builds strings as `<prefix><rangedelim><suffixes>`. The delimiter is always the **last** colon. A naive `split(":", 1)` splits at the *first* colon, silently corrupting paths when the prefix contains colons — which is **worse** than the original crash.

### `test/unit/test_util_compress.py` [NEW]
- 18 unit tests (previously zero coverage) across 5 test classes:
  - **Round-trip tests**: `compress_list → decompress_list` for various path lists
  - **Multi-colon tests**: Airflow timestamps, double colons, triple colons
  - **Edge cases**: Empty string, single item, comma-separated without prefix
  - **Validation tests**: `compress_list` correctly rejects delimiter characters
  - **Zlib tests**: Large lists trigger compression, `zlibmin=inf` prevents it

## Reproduction evidence

Chaos test comparing 3 implementations across 6 scenarios:

| Test | ORIGINAL | `split(:,1)` (WRONG) | `rsplit(:,1)` (CORRECT) |
|------|----------|---------------------|------------------------|
| Simple paths | ✓ PASS | ✓ PASS | ✓ PASS |
| Airflow multi-colon | ✗ CRASH ValueError | ✗ FAIL (corrupted data) | ✓ PASS |
| Empty string | ✗ CRASH IndexError | ✓ PASS | ✓ PASS |
| Step Functions foreach | ✓ PASS | ✓ PASS | ✓ PASS |
| Prefix with colons `job:task::A,B` | ✗ CRASH ValueError | ✗ FAIL → `['jobtask::A', 'jobB']` | ✓ PASS |
| Single item | ✓ PASS | ✓ PASS | ✓ PASS |

## Test results

### Unit tests (18/18 passed)

```
18 passed in 8.31s

test/unit/test_util_compress.py::TestDecompressListRoundTrip::test_round_trip[simple_common_prefix] PASSED
test/unit/test_util_compress.py::TestDecompressListRoundTrip::test_round_trip[single_item] PASSED
test/unit/test_util_compress.py::TestDecompressListRoundTrip::test_round_trip[two_items] PASSED
test/unit/test_util_compress.py::TestDecompressListRoundTrip::test_round_trip[no_common_prefix] PASSED
test/unit/test_util_compress.py::TestDecompressListRoundTrip::test_round_trip[partial_prefix] PASSED
test/unit/test_util_compress.py::TestDecompressListRoundTrip::test_round_trip[long_pathspecs] PASSED
test/unit/test_util_compress.py::TestDecompressListMultiColon::test_multi_colon_input[single_colon_normal] PASSED
test/unit/test_util_compress.py::TestDecompressListMultiColon::test_multi_colon_input[airflow_timestamp_colons] PASSED
test/unit/test_util_compress.py::TestDecompressListMultiColon::test_multi_colon_input[double_colon_prefix_ends_with_delim] PASSED
test/unit/test_util_compress.py::TestDecompressListMultiColon::test_multi_colon_input[triple_colon_extreme] PASSED
test/unit/test_util_compress.py::TestDecompressListEdgeCases::test_empty_string_returns_empty_list PASSED
test/unit/test_util_compress.py::TestDecompressListEdgeCases::test_single_item_no_delimiter PASSED
test/unit/test_util_compress.py::TestDecompressListEdgeCases::test_comma_separated_no_prefix PASSED
test/unit/test_util_compress.py::TestCompressListValidation::test_rejects_item_with_separator PASSED
test/unit/test_util_compress.py::TestCompressListValidation::test_rejects_item_with_rangedelim PASSED
test/unit/test_util_compress.py::TestCompressListValidation::test_rejects_item_with_zlibmarker PASSED
test/unit/test_util_compress.py::TestCompressListZlib::test_large_list_triggers_zlib PASSED
test/unit/test_util_compress.py::TestCompressListZlib::test_zlibmin_inf_prevents_compression PASSED
```

### Integration tests (14/14 passed)

Exercises `decompress_list` through the **real `metaflow.util` import** (same path as `step_cmd.py`) with inputs modeled after each cloud orchestrator:

```
INTEGRATION TEST: decompress_list fix at CLI execution boundary
Python: 3.12.3 (main, Jan 22 2026, 20:57:42) [GCC 13.3.0]
Import: metaflow.util (same as step_cmd.py)

[1] Step Functions — split-join input paths (step_functions.py:694)
  ✓ SFN split-join (single colon)
  ✓ SFN foreach-join path

[2] Argo Workflows — conditional input paths (conditional_input_paths.py:15)
  ✓ Argo timestamp run-id (multi-colon)
  ✓ Argo ISO timestamp (3 colons total)

[3] Airflow-style paths (proving hash workaround is now unnecessary)
  ✓ Airflow manual trigger run-id
  ✓ Airflow scheduled run-id (with timezone)

[4] Round-trip: compress_list → decompress_list
  ✓ Simple pathspecs / Single path / No common prefix / Long paths

[5] Edge cases
  ✓ Empty string / Single item / Comma-separated

[6] Zlib-compressed large lists
  ✓ Zlib round-trip (500 paths)

RESULTS: 14/14 passed, 0 failed, 0 crashed
```

## Risk assessment

- **Minimal risk**: `rsplit(x, 1)` is a strict behavioral superset of `split(x)` for single-delimiter inputs — all existing paths that worked before continue to work identically
- **No new dependencies**: Pure Python string operation change
- **Backward compatible**: No API changes, no new parameters
