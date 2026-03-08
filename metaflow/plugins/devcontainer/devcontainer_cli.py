import json
import os
import shutil
import subprocess
import sys
import tempfile

from metaflow import util
from metaflow._vendor import click
from metaflow.exception import MetaflowException


@click.group()
def cli():
    pass


@cli.group(help="Commands related to Devcontainer sandboxes.")
def devcontainer():
    pass


@devcontainer.command(
    help="Execute a single step inside a devcontainer sandbox. "
    "Typically you do not call this command directly; it is used "
    "internally by Metaflow when a step has the @devcontainer decorator."
)
@click.argument("step-name")
@click.option("--run-id", required=True, help="Metaflow Run ID.")
@click.option("--task-id", required=True, help="Metaflow Task ID.")
@click.option("--input-paths", default=None, help="Input artifact paths.")
@click.option("--split-index", default=None, help="Split index for foreach.")
@click.option("--retry-count", default=0, type=int, help="Retry attempt number.")
@click.option("--max-user-code-retries", default=0, type=int)
@click.option("--tag", multiple=True, help="Tags.")
@click.option("--namespace", default=None, help="Metaflow namespace.")
@click.option("--ubf-context", default=None, help="UBF context.")
@click.option("--flow-file", required=True, help="Absolute path to flow script.")
@click.option("--datastore-type", default="local", help="Datastore type.")
@click.option("--metadata-type", default="local", help="Metadata type.")
@click.option("--image", default=None, help="Container image.")
@click.option("--devcontainer-json", default=None)
@click.option("--packages", default="{}", help="JSON dict of packages.")
@click.option("--python", default=None, help="Python version.")
@click.option("--network/--no-network", default=True, help="Network access.")
@click.option("--memory", default=None, help="Memory limit.")
@click.option("--cpu", default=None, type=int, help="CPU limit.")
@click.option(
    "--backend", default="devcontainer",
    type=click.Choice(["devcontainer", "devpod", "daytona"]),
)
@click.option(
    "--security", default="none",
    type=click.Choice(["none", "standard", "strict"]),
    help="Sandbox security level.",
)
@click.pass_context
def step(
    ctx, step_name, run_id, task_id, input_paths, split_index,
    retry_count, max_user_code_retries, tag, namespace, ubf_context,
    flow_file, datastore_type, metadata_type,
    image, devcontainer_json, packages, python, network, memory, cpu,
    backend, security,
):
    """Execute a Metaflow step inside a devcontainer sandbox."""
    from .backends import get_backend
    from .devcontainer_config import (
        CONTAINER_METAFLOW_HOME,
        generate_devcontainer_json,
        load_user_devcontainer_json,
        write_devcontainer_json,
    )

    try:
        pkgs = json.loads(packages) if isinstance(packages, str) else packages
    except json.JSONDecodeError:
        pkgs = {}

    # Resolve paths
    flow_dir = os.path.dirname(os.path.abspath(flow_file))
    flow_basename = os.path.basename(flow_file)
    container_workspace = "/workspace"
    container_flow_file = os.path.join(container_workspace, flow_basename)

    click.echo("[devcontainer] Flow: %s → %s" % (flow_file, container_flow_file))
    click.echo("[devcontainer] Backend: %s" % backend)

    workspace_dir = tempfile.mkdtemp(prefix="metaflow_devcontainer_")
    backend_impl = get_backend(backend)

    workspace_id = None
    try:
        # 1. Generate devcontainer.json
        if devcontainer_json:
            config = load_user_devcontainer_json(devcontainer_json)
            _ensure_mounts(config, flow_dir, container_workspace)
            config_path = write_devcontainer_json(workspace_dir, config)
        else:
            config = generate_devcontainer_json(
                step_name=step_name, image=image, packages=pkgs,
                python_version=python, network=network,
                memory=memory, cpu=cpu, security=security,
            )
            config["mounts"].append(
                {"source": flow_dir, "target": container_workspace, "type": "bind"}
            )
            config_path = write_devcontainer_json(workspace_dir, config)

        click.echo("[devcontainer] Config:\n%s" % json.dumps(config, indent=2))

        # 2. Start container via backend
        click.echo("[devcontainer] Building container...")
        workspace_id = backend_impl.up(workspace_dir, config_path)

        # 3. Build the step command for inside the container
        step_kwargs = {
            "run_id": run_id,
            "task_id": task_id,
            "input_paths": input_paths,
            "split_index": split_index,
            "retry_count": retry_count,
            "max_user_code_retries": max_user_code_retries,
            "tag": tag if tag else None,
            "namespace": namespace,
            "ubf_context": ubf_context,
        }

        # Top-level args from parent context (the @kubernetes pattern)
        top_params = dict(ctx.parent.parent.params) if ctx.parent and ctx.parent.parent else {}

        # Override datastore root for container path
        top_params["datastore_root"] = CONTAINER_METAFLOW_HOME
        top_params["pylint"] = False

        # ---- Strip devcontainer from --with/decospecs ----
        if "decospecs" in top_params and top_params["decospecs"]:
            top_params["decospecs"] = tuple(
                spec for spec in top_params["decospecs"]
                if not spec.startswith("devcontainer")
            )
            if not top_params["decospecs"]:
                top_params["decospecs"] = None

        top_args = " ".join(util.dict_to_cli_options(top_params))
        step_args = " ".join(util.dict_to_cli_options(step_kwargs))

        step_cli = "python3 -u %s %s step %s %s" % (
            container_flow_file, top_args, step_name, step_args,
        )

        # ---- FIX: File Permissions on Bind Mount ----
        # The container usually runs as root. Files created in the local
        # datastore will be owned by root:root with 0600 permissions,
        # causing the host runtime to crash with "Permission denied".
        # We run chown inside the container after the step finishes.
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000

        fix_perms = "chown -R %s:%s %s/*/%s %s/*/data 2>/dev/null" % (
            uid, gid, CONTAINER_METAFLOW_HOME, run_id, CONTAINER_METAFLOW_HOME
        )

        # Wrap in bash to preserve the step's exit code
        wrapped_cli = [
            "bash", "-c",
            "%s ; err=$? ; %s ; exit $err" % (step_cli, fix_perms)
        ]

        click.echo("[devcontainer] Step CLI:\n  %s" % step_cli)

        # 4. Execute step via backend
        exit_code = backend_impl.exec(workspace_id, wrapped_cli)

        if exit_code != 0:
            raise MetaflowException(
                "Step '%s' failed inside devcontainer with exit code %d"
                % (step_name, exit_code)
            )

    finally:
        click.echo("[devcontainer] Cleaning up...")
        if workspace_id is not None:
            backend_impl.down(workspace_id)
        shutil.rmtree(workspace_dir, ignore_errors=True)


def _ensure_mounts(config, flow_dir, container_workspace):
    from .devcontainer_config import CONTAINER_METAFLOW_HOME
    metaflow_home = os.path.join(os.path.expanduser("~"), ".metaflow")
    if "mounts" not in config:
        config["mounts"] = []
    if "containerEnv" not in config:
        config["containerEnv"] = {}
    targets = [m.get("target") for m in config["mounts"] if isinstance(m, dict)]
    if CONTAINER_METAFLOW_HOME not in targets:
        config["mounts"].append(
            {"source": metaflow_home, "target": CONTAINER_METAFLOW_HOME, "type": "bind"}
        )
    if container_workspace not in targets:
        config["mounts"].append(
            {"source": flow_dir, "target": container_workspace, "type": "bind"}
        )
    config["containerEnv"]["METAFLOW_DATASTORE_SYSROOT_LOCAL"] = CONTAINER_METAFLOW_HOME
    config["containerEnv"]["METAFLOW_DEVCONTAINER"] = "1"
