"""
End-to-End test for the decompress_list multi-colon fix (Issue #2908, PR #2909).

This test proves the bug by launching a **real Metaflow FlowSpec** through the
actual CLI engine via subprocess — the exact same code path that cloud
orchestrators (Airflow, Argo Workflows, AWS Step Functions) use when they
invoke a step during deployment/run.

The key command replicated here is:
    python <flow>.py step <step_name> --run-id <id> --task-id <id> \
        --input-paths "<compressed_string_with_multi_colons>"

Without the rsplit fix, decompress_list() at step_cmd.py:150 crashes with:
    ValueError: too many values to unpack (expected 2)

With the fix, decompress_list() correctly splits at the *last* colon and the
step proceeds past the decompression stage.

See: https://github.com/Netflix/metaflow/issues/2908
"""

import os
import sys
import subprocess
import tempfile
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FLOW_SOURCE = textwrap.dedent(
    """\
    from metaflow import FlowSpec, step

    class ColonBugFlow(FlowSpec):
        \"\"\"Minimal flow with a start → end topology.\"\"\"

        @step
        def start(self):
            self.next(self.end)

        @step
        def end(self):
            print("end step reached")

    if __name__ == "__main__":
        ColonBugFlow()
    """
)


def _run_step_with_input_paths(flow_file, input_paths_str):
    """
    Invoke the Metaflow CLI ``step`` command on *flow_file* exactly the way
    a cloud orchestrator would, passing *input_paths_str* as the compressed
    ``--input-paths`` argument.

    Returns (returncode, stdout, stderr).
    """
    cmd = [
        sys.executable,
        flow_file,
        "--no-pylint",
        "step",
        "end",                          # target step
        "--run-id", "e2e-test-run",
        "--task-id", "1",
        "--input-paths", input_paths_str,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def flow_file(tmp_path):
    """Write the minimal FlowSpec to a temp directory and return its path."""
    fpath = os.path.join(str(tmp_path), "colon_bug_flow.py")
    with open(fpath, "w") as f:
        f.write(FLOW_SOURCE)
    return fpath


# ---------------------------------------------------------------------------
# E2E Tests
# ---------------------------------------------------------------------------

class TestDecompressListE2E:
    """
    Each test case launches a real Metaflow step via subprocess with a
    compressed ``--input-paths`` string that contains multiple colons —
    exactly the payload produced by cloud orchestrators.

    The assertion checks that the CLI does **not** crash with the old
    ``ValueError: too many values to unpack`` bug.  Any *other* error
    (e.g. missing datastore) is acceptable — it proves the
    decompression stage succeeded.
    """

    CRASH_SIGNATURE = "too many values to unpack"

    # -- Airflow-style timestamp run-id ------------------------------------

    def test_airflow_timestamp_input_paths(self, flow_file):
        """
        Airflow produces run-ids like ``manual__2022-03-15T01:26:41``.
        When compress_list encodes paths with such a prefix, the
        compressed string contains multiple colons.

        Without the fix → ValueError at decompress_list().
        With the fix   → step proceeds past decompression.
        """
        # Simulates compressed output for paths:
        #   manual__2022-03-15T01:26:41/start/task1
        #   manual__2022-03-15T01:26:41/start/task2
        # Compressed form: <prefix>:<suffixes>
        input_paths = (
            "manual__2022-03-15T01:26:41/start/:task1,task2"
        )
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, input_paths)
        assert self.CRASH_SIGNATURE not in stderr, (
            f"decompress_list crashed with the old ValueError bug!\n"
            f"STDERR:\n{stderr}"
        )

    # -- Double-colon (prefix ends with delimiter char) --------------------

    def test_double_colon_prefix(self, flow_file):
        """
        Input like ``job:task::A,B`` — the prefix itself ends with the
        delimiter character, producing a double colon.

        Compressed form for paths ["job:task:A", "job:task:B"]:
            "job:task::A,B"
        """
        input_paths = "job:task::A,B"
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, input_paths)
        assert self.CRASH_SIGNATURE not in stderr, (
            f"decompress_list crashed with the old ValueError bug!\n"
            f"STDERR:\n{stderr}"
        )

    # -- Triple-colon extreme case -----------------------------------------

    def test_triple_colon_extreme(self, flow_file):
        """
        Extreme case: ``a:b:c::x,y`` — three colons in the compressed
        string.
        """
        input_paths = "a:b:c::x,y"
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, input_paths)
        assert self.CRASH_SIGNATURE not in stderr, (
            f"decompress_list crashed with the old ValueError bug!\n"
            f"STDERR:\n{stderr}"
        )

    # -- Argo Workflows-style payload --------------------------------------

    def test_argo_style_input_paths(self, flow_file):
        """
        Argo Workflows passes input paths through
        ``argo/conditional_input_paths.py`` which calls decompress_list.
        If the flow's run-id contains colons, the compressed payload
        will too.

        Simulated paths:
            argo-wf:run:2024-01/train/task_001
            argo-wf:run:2024-01/train/task_002
        """
        input_paths = "argo-wf:run:2024-01/train/:task_001,task_002"
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, input_paths)
        assert self.CRASH_SIGNATURE not in stderr, (
            f"decompress_list crashed with the old ValueError bug!\n"
            f"STDERR:\n{stderr}"
        )

    # -- Empty string guard ------------------------------------------------

    def test_empty_input_paths(self, flow_file):
        """
        An empty ``--input-paths`` string should not crash with
        ``IndexError: string index out of range`` (the second bug
        fixed in the PR).
        """
        input_paths = ""
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, input_paths)
        assert "string index out of range" not in stderr, (
            f"decompress_list crashed with the old IndexError bug!\n"
            f"STDERR:\n{stderr}"
        )

    # -- Normal single-colon (sanity check) --------------------------------

    def test_normal_single_colon_still_works(self, flow_file):
        """
        A normal compressed string with a single colon (the happy path)
        must still work after the rsplit fix.
        """
        input_paths = "sfn-abc123/train/:task_001,task_002,task_003"
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, input_paths)
        assert self.CRASH_SIGNATURE not in stderr, (
            f"Normal single-colon case unexpectedly failed!\n"
            f"STDERR:\n{stderr}"
        )


class TestDecompressRoundTripE2E:
    """
    Verify that compress_list → decompress_list round-trips preserve
    data integrity, and then show the decompress result is exactly
    what the Metaflow step CLI would receive.

    This is a semi-E2E test: it uses the real compress/decompress
    functions (not mocks) but validates the data at the boundary
    that matters (step_cmd.py line 150).
    """

    def test_round_trip_then_cli_invocation(self, flow_file):
        """
        1. compress_list a set of normal paths
        2. decompress_list the result
        3. Assert round-trip integrity
        4. Feed the compressed string to the real CLI to prove E2E
        """
        from metaflow.util import compress_list, decompress_list

        paths = [
            "run123/train/task_001",
            "run123/train/task_002",
            "run123/train/task_003",
        ]
        compressed = compress_list(paths)
        decompressed = decompress_list(compressed)
        assert decompressed == paths, (
            f"Round-trip failed: {paths} → {compressed} → {decompressed}"
        )

        # Now feed to the real CLI
        rc, stdout, stderr = _run_step_with_input_paths(flow_file, compressed)
        assert "too many values to unpack" not in stderr


# ---------------------------------------------------------------------------
# Direct-invocation support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
