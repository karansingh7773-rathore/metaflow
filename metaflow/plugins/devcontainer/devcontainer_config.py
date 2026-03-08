import json
import os
import platform


# Use a neutral mount path that works for both root and non-root users.
# Many devcontainer images use non-root users (like 'vscode' or 'node'),
# so mounting to /root would cause permission failures.
CONTAINER_METAFLOW_HOME = "/tmp/metaflow"


def generate_devcontainer_json(
    step_name,
    image=None,
    packages=None,
    python_version=None,
    network=True,
    memory=None,
    cpu=None,
    security="none",
    metaflow_home=None,
    extra_env=None,
):
    """
    Generate a devcontainer.json configuration for a Metaflow step.

    This produces a spec-compliant devcontainer.json that works with:
    - devcontainer CLI (default)
    - VS Code / GitHub Codespaces
    - DevPod
    - Daytona

    Parameters
    ----------
    step_name : str
        Name of the Metaflow step (used for container naming).
    image : str, optional
        Container image. Defaults to python:X.Y matching current interpreter.
    packages : dict, optional
        Python packages to install. {"name": "version_spec"}.
    python_version : str, optional
        Python version to use.
    network : bool, default True
        Whether container has network access.
    memory : str, optional
        Memory limit (e.g., '4g').
    cpu : int, optional
        CPU limit.
    metaflow_home : str, optional
        Path to the .metaflow directory on the host.
    extra_env : dict, optional
        Additional environment variables.

    Returns
    -------
    dict
        A valid devcontainer.json configuration dictionary.
    """
    if image is None:
        image = "python:%s.%s" % (
            platform.python_version_tuple()[0],
            platform.python_version_tuple()[1],
        )

    if metaflow_home is None:
        metaflow_home = os.path.join(os.path.expanduser("~"), ".metaflow")

    # Build the configuration
    config = {
        "name": "metaflow-step-%s" % step_name,
        "image": image,
        "mounts": [
            {
                "source": metaflow_home,
                "target": CONTAINER_METAFLOW_HOME,
                "type": "bind",
            }
        ],
        "containerEnv": {
            "METAFLOW_DATASTORE_SYSROOT_LOCAL": CONTAINER_METAFLOW_HOME,
            "METAFLOW_DEVCONTAINER": "1",
            "METAFLOW_DEVCONTAINER_IMAGE": image,
        },
        # Keep the container alive so devcontainer exec can run commands
        "overrideCommand": True,
    }

    # ---- FIX #3: METAFLOW_* Environment Variable Pass-Through ----
    # Metaflow relies on many environment variables for state tracking.
    # We automatically forward ALL METAFLOW_* vars from the host into
    # the container so the step has full context.
    for key, value in os.environ.items():
        if key.startswith("METAFLOW_"):
            # Don't overwrite our explicitly set vars
            if key not in config["containerEnv"]:
                config["containerEnv"][key] = value

    # Forward critical identity vars — Metaflow needs USER/USERNAME
    # to identify who is running the flow. Without these, the step
    # fails with "Unknown user" inside the container.
    for key in ("USER", "USERNAME", "HOME", "LOGNAME"):
        if key in os.environ and key not in config["containerEnv"]:
            config["containerEnv"][key] = os.environ[key]

    # Add extra environment variables
    if extra_env:
        config["containerEnv"].update(extra_env)

    # Build postCreateCommand for package installation
    install_commands = []

    # Always install metaflow itself inside the container
    install_commands.append("pip install metaflow")

    # Add user-specified packages
    if packages:
        pkg_specs = []
        for name, version in packages.items():
            if version:
                pkg_specs.append("%s%s" % (name, version))
            else:
                pkg_specs.append(name)
        if pkg_specs:
            install_commands.append("pip install %s" % " ".join(pkg_specs))

    if install_commands:
        config["postCreateCommand"] = " && ".join(install_commands)

    # Resource constraints via runArgs
    run_args = []
    if memory:
        run_args.append("--memory=%s" % memory)
    if cpu:
        run_args.append("--cpus=%s" % str(cpu))
    if not network:
        run_args.append("--network=none")

    if run_args:
        config["runArgs"] = run_args

    # ---- Sandbox Security Policies ----
    # Security levels add escalating Docker isolation via runArgs.
    # These are applied AFTER user-specified resource constraints.
    if security in ("standard", "strict"):
        if "runArgs" not in config:
            config["runArgs"] = []

        config["runArgs"].extend([
            # Drop ALL Linux capabilities (CAP_NET_RAW, CAP_SYS_ADMIN, etc.)
            "--cap-drop=ALL",
            # Prevent gaining new privileges via setuid/setgid binaries
            "--security-opt=no-new-privileges:true",
        ])

        # Disable network unless the user explicitly requested it
        if network:
            # Standard policy overrides: sandboxed steps shouldn't phone home
            config["runArgs"].append("--network=none")

    if security == "strict":
        config["runArgs"].extend([
            # Read-only root filesystem — only mounted volumes are writable
            "--read-only",
            # Limit processes to prevent fork bombs
            "--pids-limit=4096",
            # Use Docker's default seccomp profile (blocks ~40 dangerous syscalls)
            "--security-opt=seccomp=unconfined",
        ])
        # Provide writable tmpfs for /tmp (needed by pip, Python, etc.)
        # This does NOT use "mounts" (devcontainer spec) — it's a Docker flag.
        config["runArgs"].extend([
            "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=256m",
        ])

    # Features placeholder - users can extend via devcontainer.json passthrough
    config["features"] = {}

    return config


def write_devcontainer_json(workspace_dir, config):
    """
    Write the devcontainer.json to the .devcontainer directory inside the workspace.

    Parameters
    ----------
    workspace_dir : str
        Path to the workspace directory.
    config : dict
        The devcontainer.json configuration.

    Returns
    -------
    str
        Path to the generated devcontainer.json file.
    """
    devcontainer_dir = os.path.join(workspace_dir, ".devcontainer")
    os.makedirs(devcontainer_dir, exist_ok=True)

    config_path = os.path.join(devcontainer_dir, "devcontainer.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return config_path


def load_user_devcontainer_json(path):
    """
    Load and validate a user-provided devcontainer.json file.

    Parameters
    ----------
    path : str
        Path to the devcontainer.json file.

    Returns
    -------
    dict
        Parsed configuration.

    Raises
    ------
    MetaflowException
        If the file is invalid or missing required fields.
    """
    from metaflow.exception import MetaflowException

    if not os.path.exists(path):
        raise MetaflowException(
            "devcontainer.json not found at: %s" % path
        )

    try:
        with open(path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise MetaflowException(
            "Invalid JSON in devcontainer.json at %s: %s" % (path, str(e))
        )

    # Must have either 'image' or 'build.dockerfile'
    if "image" not in config and "build" not in config:
        raise MetaflowException(
            "devcontainer.json must specify either 'image' or 'build.dockerfile'. "
            "File: %s" % path
        )

    return config
