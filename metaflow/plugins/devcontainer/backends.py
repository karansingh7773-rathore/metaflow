"""
Execution backends for the @devcontainer decorator.

Each backend wraps a different CLI tool that consumes devcontainer.json:
- DevcontainerBackend: @devcontainers/cli (default, reference implementation)
- DevPodBackend:       DevPod (loft-sh/devpod)
- DaytonaBackend:      Daytona (daytonaio/daytona)

All three consume the *same* devcontainer.json spec, but differ in how they
start, exec into, and tear down containers.
"""

import json
import os
import re
import shutil
import subprocess
import sys

from metaflow._vendor import click
from metaflow.exception import MetaflowException


class ContainerBackend:
    """
    Abstract base class for devcontainer execution backends.

    A backend must implement three operations:

    1. ``up``   – Start a container from a workspace with devcontainer.json.
    2. ``exec`` – Run a command inside the running container.
    3. ``down`` – Tear down the container (best-effort, never raises).

    ``up`` returns a *workspace_id* that is opaque to the caller — the
    devcontainer backend uses the workspace folder path, while DevPod and
    Daytona use a workspace name string.
    """

    name = None          # e.g. "devcontainer"
    cli_command = None   # binary expected on PATH, e.g. "devcontainer"

    # ---- Install hints (shown when CLI is missing) ----
    install_hint = "Please install the required CLI tool."

    @classmethod
    def is_available(cls):
        """Return True if the backend CLI is found on PATH."""
        return shutil.which(cls.cli_command) is not None

    def up(self, workspace_dir, config_path):
        """
        Start the container.

        Parameters
        ----------
        workspace_dir : str
            Temporary directory containing ``.devcontainer/devcontainer.json``.
        config_path : str
            Absolute path to the generated ``devcontainer.json``.

        Returns
        -------
        str
            An opaque *workspace_id* to pass to ``exec`` and ``down``.

        Raises
        ------
        MetaflowException
            If the container fails to start.
        """
        raise NotImplementedError

    def exec(self, workspace_id, cmd):
        """
        Execute *cmd* inside the running container.

        Parameters
        ----------
        workspace_id : str
            The identifier returned by ``up``.
        cmd : list[str]
            Command to execute (argv style).

        Returns
        -------
        int
            Exit code of the executed command.
        """
        raise NotImplementedError

    def down(self, workspace_id):
        """
        Tear down the container.  Best-effort — must never raise.

        Parameters
        ----------
        workspace_id : str
            The identifier returned by ``up``.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# devcontainer CLI  (@devcontainers/cli)
# ---------------------------------------------------------------------------

class DevcontainerBackend(ContainerBackend):
    """
    Default backend using the official devcontainer CLI.

    CLI reference: https://github.com/devcontainers/cli

    Lifecycle:
        devcontainer up   --workspace-folder <dir>
        devcontainer exec --workspace-folder <dir> -- <cmd>
        docker rm -f <container-id>                        (cleanup)
    """

    name = "devcontainer"
    cli_command = "devcontainer"
    install_hint = (
        "The @devcontainer decorator requires the devcontainer CLI.\n"
        "Install it with:\n"
        "  npm install -g @devcontainers/cli\n"
        "Or:\n"
        "  curl -fsSL https://raw.githubusercontent.com/devcontainers/"
        "cli/main/scripts/install.sh | sh"
    )

    def up(self, workspace_dir, config_path):
        cmd = ["devcontainer", "up", "--workspace-folder", workspace_dir]
        result = self._run(cmd, timeout=300)

        # Parse output JSON to extract container ID (nice-to-have)
        container_id = None
        if result.stdout.strip():
            try:
                out = json.loads(result.stdout.strip().split("\n")[-1])
                if out.get("outcome") == "success":
                    container_id = out.get("containerId", "")
                    click.echo(
                        "[devcontainer] Container ID: %s" % container_id[:12]
                    )
                elif out.get("outcome") == "error":
                    raise MetaflowException(
                        "devcontainer up failed: %s" % out.get("description", "")
                    )
            except (json.JSONDecodeError, IndexError):
                pass

        click.echo("[devcontainer] Container started.")
        # workspace_id = workspace folder path for this backend
        return workspace_dir

    def exec(self, workspace_id, cmd):
        full_cmd = [
            "devcontainer", "exec",
            "--workspace-folder", workspace_id,
            "--",
        ] + cmd
        return self._stream(full_cmd)

    def down(self, workspace_id):
        try:
            result = subprocess.run(
                ["docker", "ps", "-q", "--filter",
                 "label=devcontainer.local_folder=%s" % workspace_id],
                capture_output=True, text=True, timeout=10,
            )
            for cid in result.stdout.strip().split("\n"):
                if cid.strip():
                    subprocess.run(
                        ["docker", "rm", "-f", cid.strip()],
                        capture_output=True, timeout=10,
                    )
        except Exception:
            pass

    # -- helpers --

    @staticmethod
    def _run(cmd, timeout=300):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                raise MetaflowException(
                    "%s failed (exit %d).\nstdout: %s\nstderr: %s"
                    % (cmd[0], result.returncode,
                       result.stdout[-500:], result.stderr[-500:])
                )
            return result
        except subprocess.TimeoutExpired:
            raise MetaflowException(
                "%s timed out after %d seconds." % (cmd[0], timeout)
            )

    @staticmethod
    def _stream(cmd):
        """Run *cmd* and stream stdout to the console in real time."""
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        process.wait()
        return process.returncode


# ---------------------------------------------------------------------------
# DevPod  (loft-sh/devpod)
# ---------------------------------------------------------------------------

class DevPodBackend(ContainerBackend):
    """
    Backend using DevPod.

    CLI reference: https://devpod.sh/docs/developing-in-workspaces/

    Lifecycle:
        devpod up <workspace-dir>
        devpod ssh <workspace-name> --command "<cmd>"
        devpod delete <workspace-name>

    DevPod auto-generates a *workspace name* from the directory basename.
    We capture it from ``devpod up`` output to use in ssh/delete.
    """

    name = "devpod"
    cli_command = "devpod"
    install_hint = (
        "The @devcontainer decorator with backend='devpod' requires DevPod.\n"
        "Install it from: https://devpod.sh/docs/getting-started/install"
    )

    def up(self, workspace_dir, config_path):
        # DevPod derives the workspace name from the directory basename.
        workspace_name = os.path.basename(workspace_dir).lower()
        # Sanitise to valid workspace name (alphanumeric + hyphens)
        workspace_name = re.sub(r"[^a-z0-9]", "-", workspace_name)
        workspace_name = re.sub(r"-+", "-", workspace_name).strip("-")

        cmd = [
            "devpod", "up", workspace_dir,
            "--devcontainer-path",
            os.path.join(workspace_dir, ".devcontainer", "devcontainer.json"),
            "--ide", "none",          # headless — no IDE
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                raise MetaflowException(
                    "devpod up failed (exit %d).\nstdout: %s\nstderr: %s"
                    % (result.returncode,
                       result.stdout[-500:], result.stderr[-500:])
                )
        except subprocess.TimeoutExpired:
            raise MetaflowException("devpod up timed out after 5 minutes.")

        click.echo("[devpod] Workspace '%s' started." % workspace_name)
        return workspace_name

    def exec(self, workspace_id, cmd):
        full_cmd = [
            "devpod", "ssh", workspace_id,
            "--command", " ".join(cmd),
        ]
        process = subprocess.Popen(
            full_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        process.wait()
        return process.returncode

    def down(self, workspace_id):
        try:
            subprocess.run(
                ["devpod", "delete", workspace_id, "--force"],
                capture_output=True, timeout=30,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Daytona  (daytonaio/daytona)
# ---------------------------------------------------------------------------

class DaytonaBackend(ContainerBackend):
    """
    Backend using Daytona.

    CLI reference: https://www.daytona.io/docs/usage/cli/

    Lifecycle:
        daytona create <workspace-dir> --devcontainer-path <path>
        daytona exec <workspace-name> -- <cmd>
        daytona delete <workspace-name> -y

    Daytona also auto-generates workspace names; we parse ``daytona create``
    output to capture it.
    """

    name = "daytona"
    cli_command = "daytona"
    install_hint = (
        "The @devcontainer decorator with backend='daytona' requires Daytona.\n"
        "Install it from: https://www.daytona.io/docs/installation/installation/"
    )

    def up(self, workspace_dir, config_path):
        cmd = [
            "daytona", "create", workspace_dir,
            "--devcontainer-path", config_path,
            "--no-ide",               # headless
            "-y",                     # auto-confirm
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                raise MetaflowException(
                    "daytona create failed (exit %d).\nstdout: %s\nstderr: %s"
                    % (result.returncode,
                       result.stdout[-500:], result.stderr[-500:])
                )
        except subprocess.TimeoutExpired:
            raise MetaflowException("daytona create timed out after 5 minutes.")

        # Try to extract workspace name from output.
        # Daytona typically prints "Workspace <name> created" or similar.
        workspace_name = os.path.basename(workspace_dir).lower()
        workspace_name = re.sub(r"[^a-z0-9]", "-", workspace_name)
        workspace_name = re.sub(r"-+", "-", workspace_name).strip("-")

        # Attempt to find the workspace name from Daytona output
        for line in result.stdout.split("\n"):
            # e.g. "Workspace 'my-workspace' created successfully"
            match = re.search(r"[Ww]orkspace\s+['\"]?(\S+?)['\"]?\s+created", line)
            if match:
                workspace_name = match.group(1)
                break

        click.echo("[daytona] Workspace '%s' created." % workspace_name)
        return workspace_name

    def exec(self, workspace_id, cmd):
        full_cmd = ["daytona", "exec", workspace_id, "--"] + cmd
        process = subprocess.Popen(
            full_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        process.wait()
        return process.returncode

    def down(self, workspace_id):
        try:
            subprocess.run(
                ["daytona", "delete", workspace_id, "-y"],
                capture_output=True, timeout=30,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_BACKENDS = {
    "devcontainer": DevcontainerBackend,
    "devpod": DevPodBackend,
    "daytona": DaytonaBackend,
}

VALID_BACKENDS = tuple(_BACKENDS.keys())


def get_backend(name):
    """
    Return an instance of the requested backend.

    Parameters
    ----------
    name : str
        One of ``"devcontainer"``, ``"devpod"``, ``"daytona"``.

    Returns
    -------
    ContainerBackend

    Raises
    ------
    MetaflowException
        If the backend name is unknown.
    """
    cls = _BACKENDS.get(name)
    if cls is None:
        raise MetaflowException(
            "Unknown devcontainer backend: '%s'. Choose from: %s"
            % (name, ", ".join(VALID_BACKENDS))
        )
    return cls()
