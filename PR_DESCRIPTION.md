The `@timeout` decorator relies on `signal.SIGALRM` (POSIX-only). On Windows, this caused an unhelpful `AttributeError`. This adds an early platform check in `step_init()` that raises a descriptive `MetaflowException`, guiding users to run on Linux, macOS, or WSL.

Includes unit tests that verify the error by temporarily removing `signal.SIGALRM`.

## PR Type

- [x] Bug fix
- [ ] New feature
- [ ] Core Runtime change (higher bar -- see [CONTRIBUTING.md](../CONTRIBUTING.md#core-runtime-contributions-higher-bar))
- [ ] Docs / tooling
- [ ] Refactoring

## Summary

Users who apply `@timeout` on Windows get a cryptic `AttributeError: module 'signal' has no attribute 'SIGALRM'` with no guidance. After this fix, they get a clear `MetaflowException` explaining that `@timeout` requires POSIX signals and suggesting Linux, macOS, or WSL as alternatives.

## Issue

Fixes #2873

## Reproduction

**Runtime:** local (Windows native, Python 3.11.9)

**Commands to run:**
```bash
# Minimal reproduction — run on native Windows (not WSL)
python -c "import signal; signal.signal(signal.SIGALRM, lambda s,f: None)"
```

**Where evidence shows up:** parent console (immediate crash on import)

<details>
<summary>Before (error / log snippet)</summary>

```
Platform: win32
Python:   3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
Has SIGALRM: False

--- Reproducing timeout_decorator.py line 73 crash ---
  AttributeError: module 'signal' has no attribute 'SIGALRM'

--- Reproducing timeout_decorator.py line 74 crash ---
  AttributeError: module 'signal' has no attribute 'alarm'
```

The user sees a raw `AttributeError` with no explanation of what went wrong or how to fix it.

</details>

<details>
<summary>After (evidence that fix works)</summary>

```
MetaflowException: The @timeout decorator for step *start* is not supported on win32.
The @timeout decorator relies on POSIX signals (SIGALRM) which are not available on
this platform. Please run your flow on Linux, macOS, or Windows Subsystem for Linux (WSL).
```

The error is now caught at graph validation time (`step_init`), before the flow starts running, with a clear message and actionable alternatives.

</details>

## Root Cause

`TimeoutDecorator.task_pre_step()` unconditionally calls `signal.signal(signal.SIGALRM, ...)` and `signal.alarm()` at line 73–74 of `timeout_decorator.py`. These are POSIX-only APIs defined by the Unix signal specification. On Windows, the `signal` module exists but does not expose `SIGALRM` or `alarm()`, causing an `AttributeError` at runtime with no framework-level handling.

## Why This Fix Is Correct

- **Earliest detection point:** The check is in `step_init()`, which runs during graph validation — before any tasks execute. This means users see the error immediately when they define their flow, not after waiting for task scheduling.
- **Minimal change:** Only adds a `hasattr(signal, "SIGALRM")` guard and `import sys`. No behavioral change on Linux/macOS where `SIGALRM` exists.
- **Consistent with existing patterns:** The existing `if not self.secs` check in the same function already uses `MetaflowException` for early validation. This follows the same pattern.

## Failure Modes Considered

1. **False positive on non-Windows POSIX systems:** The check uses `hasattr(signal, "SIGALRM")` rather than `sys.platform == "win32"`, so it correctly handles any hypothetical platform lacking SIGALRM — not just Windows.
2. **Backward compatibility on Linux/macOS:** The new code path is unreachable on any platform where `signal.SIGALRM` exists. Zero behavioral change for existing users. Verified by running all 4 tests on Linux (WSL, Python 3.12.3).

## Tests

- [x] Unit tests added/updated
- [x] Reproduction script provided (required for Core Runtime)
- [x] CI passes
- [ ] If tests are impractical: explain why below and provide manual evidence above

**Test results on Linux (WSL, Python 3.12.3):**
```
test_error_message_includes_platform PASSED
test_no_error_on_platform_with_sigalrm PASSED
test_raises_on_platform_without_sigalrm PASSED
test_zero_seconds_raises_duration_error PASSED
============================== 4 passed in 1.23s ===============================
```

**Test results on Windows (Python 3.11.9):**
```
test_error_message_includes_platform PASSED
test_no_error_on_platform_with_sigalrm SKIPPED (no SIGALRM)
test_raises_on_platform_without_sigalrm PASSED
test_zero_seconds_raises_duration_error PASSED
======================== 3 passed, 1 skipped in 0.11s =========================
```

Tests simulate Windows on Linux by temporarily removing `signal.SIGALRM` via `delattr`, then restoring it in a `finally` block.

## Non-Goals

- **Windows support for `@timeout`:** This PR does not implement a Windows-compatible timeout mechanism (e.g., threading-based). It only converts a confusing crash into a clear, actionable error message.
- **Fixing other Windows incompatibilities:** The Metaflow import chain has other POSIX dependencies (e.g., `fcntl` in `sidecar_subprocess.py`) that also fail on Windows. Those are separate issues outside the scope of this PR.

## AI Tool Usage

- [x] AI tools were used (describe below)

- **Tool:** Google Gemini (Antigravity)
- **Used for:** Codebase exploration to identify the bug, drafting the reproduction script, and generating the initial test structure.
- **All generated code was reviewed, understood, and tested** on both Windows (native, Python 3.11.9) and Linux (WSL, Python 3.12.3) before submission.
