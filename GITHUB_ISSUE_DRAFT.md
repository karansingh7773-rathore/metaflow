# `@timeout` decorator crashes with unhelpful `AttributeError` on Windows

## Summary

The `@timeout` decorator relies on `signal.SIGALRM` and `signal.alarm()`, which are POSIX-only APIs not available on Windows. When a user applies `@timeout` to a step and runs on Windows, they get a cryptic `AttributeError` instead of a helpful framework-level error explaining why it doesn't work.

## Environment

- **OS:** Windows 10/11 (native, not WSL)
- **Python:** 3.11.9 (also reproducible on 3.10+)
- **Metaflow:** current main branch

## Steps to Reproduce

```python
# test_timeout.py
from metaflow import FlowSpec, step, timeout

class TimeoutTestFlow(FlowSpec):
    @timeout(seconds=30)
    @step
    def start(self):
        import time
        time.sleep(2)
        self.next(self.end)

    @step
    def end(self):
        print("Done.")

if __name__ == "__main__":
    TimeoutTestFlow()
```

Run: `python test_timeout.py run`

## Actual Behavior

```
AttributeError: module 'signal' has no attribute 'SIGALRM'
```

The crash occurs at `timeout_decorator.py` line 73:
```python
signal.signal(signal.SIGALRM, self._sigalrm_handler)
```

This is unhelpful — it doesn't tell the user *why* it failed or what to do about it.

### Proof of crash (on Windows)

```
Platform: win32
Python:   3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
Has SIGALRM: False

--- Reproducing timeout_decorator.py line 73 crash ---
  AttributeError: module 'signal' has no attribute 'SIGALRM'

--- Reproducing timeout_decorator.py line 74 crash ---
  AttributeError: module 'signal' has no attribute 'alarm'

BUG CONFIRMED: @timeout decorator will crash on Windows
with an unhelpful AttributeError instead of a clear message.
```

## Expected Behavior

A clear, user-friendly `MetaflowException` at graph validation time (`step_init`), such as:

```
MetaflowException: The @timeout decorator for step *start* is not supported on win32.
The @timeout decorator relies on POSIX signals (SIGALRM) which are not available on
this platform. Please run your flow on Linux, macOS, or Windows Subsystem for Linux (WSL).
```

## Proposed Fix

Add a platform check in `TimeoutDecorator.step_init()` that detects the absence of `signal.SIGALRM` and raises a descriptive `MetaflowException` early — at graph validation time, before the flow even starts running.

```python
def step_init(self, flow, graph, step, decos, environment, flow_datastore, logger):
    self.logger = logger
    if not self.secs:
        raise MetaflowException("Specify a duration for @timeout.")
    if not hasattr(signal, "SIGALRM"):
        raise MetaflowException(
            "The @timeout decorator for step *%s* is not supported on %s. "
            "The @timeout decorator relies on POSIX signals (SIGALRM) which "
            "are not available on this platform. "
            "Please run your flow on Linux, macOS, or Windows Subsystem for "
            "Linux (WSL)." % (step, sys.platform)
        )
```

This catches the issue at the earliest possible point (graph init, not task execution), gives a clear explanation, and suggests actionable alternatives.

## Impact

- **Low risk:** Only adds a guard clause; no behavioral change on Linux/macOS
- **Non-Core:** This is a decorator usability improvement, not a Core Runtime change
- **Testable:** Unit tests included that verify the error message on all platforms by temporarily removing `signal.SIGALRM`

## Next Steps

I have tested this locally on Windows and WSL. I will open a draft PR shortly implementing this `step_init` guard clause and the corresponding unit tests so the team can review it!
