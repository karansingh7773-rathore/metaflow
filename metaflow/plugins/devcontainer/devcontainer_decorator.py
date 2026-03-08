import json
import os
import subprocess
import sys

from metaflow.decorators import StepDecorator
from metaflow.exception import MetaflowException


class DevcontainerDecorator(StepDecorator):
    """
    Executes this step inside a Development Container sandbox.

    Uses the Development Container Specification (https://containers.dev/)
    to create reproducible, isolated execution environments.

    Parameters
    ----------
    image : str
        Container image to use.
    devcontainer_json : str, optional
        Path to an existing devcontainer.json file.
    packages : Dict[str, str], default {}
        Python packages to install inside the container.
    python : str, optional
        Python version to use in the container.
    network : bool, default True
        Whether the container has network access.
    memory : str, optional
        Memory limit for the container, e.g. '4g'.
    cpu : int, optional
        Number of CPUs to allocate.
    backend : str, default 'devcontainer'
        Execution backend: 'devcontainer', 'devpod', or 'daytona'.
    security : str, default 'none'
        Sandbox security level:
        - 'none': No extra restrictions (default).
        - 'standard': Drop all Linux capabilities, no privilege
          escalation, disable network.
        - 'strict': Standard + read-only root filesystem, PID limit,
          default seccomp profile.
    """

    name = "devcontainer"
    defaults = {
        "image": None,
        "devcontainer_json": None,
        "packages": {},
        "python": None,
        "network": True,
        "memory": None,
        "cpu": None,
        "backend": "devcontainer",
        "security": "none",
    }

    def step_init(self, flow, graph, step, decos, environment, flow_datastore, logger):
        self.logger = logger
        self.environment = environment
        self.step = step
        self.flow_datastore = flow_datastore

        # ---- CRITICAL: Skip if already inside a devcontainer ----
        # When the step runs inside the container, the decorator is
        # still present. We detect this via METAFLOW_DEVCONTAINER env
        # and become a no-op to prevent infinite recursion.
        if os.environ.get("METAFLOW_DEVCONTAINER"):
            self._inside_container = True
            return
        self._inside_container = False

        # 1. Check: backend CLI must be installed
        from .backends import get_backend
        backend_name = self.attributes.get("backend", "devcontainer")
        backend_impl = get_backend(backend_name)

        if not backend_impl.is_available():
            raise MetaflowException(backend_impl.install_hint)

        # 2. Check: Docker daemon must be reachable
        try:
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                check=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise MetaflowException(
                "The @devcontainer decorator requires Docker.\n"
                "Install Docker and try again."
            )
        except subprocess.CalledProcessError:
            raise MetaflowException(
                "The @devcontainer decorator requires a running Docker daemon.\n"
                "Start Docker with: sudo systemctl start docker"
            )
        except subprocess.TimeoutExpired:
            raise MetaflowException(
                "Docker daemon did not respond within 10 seconds.\n"
                "Check that Docker is running: docker info"
            )

        # 3. Check: conflict with @batch/@kubernetes
        for deco in decos:
            if deco.name in ("batch", "kubernetes"):
                raise MetaflowException(
                    "Step *%s* cannot use @devcontainer together with @%s. "
                    "Choose one execution backend." % (step, deco.name)
                )

        # Set default image if not specified
        if not self.attributes["image"]:
            import platform
            self.attributes["image"] = "python:%s.%s" % (
                platform.python_version_tuple()[0],
                platform.python_version_tuple()[1],
            )

    def runtime_init(self, flow, graph, package, run_id):
        self.flow = flow
        self.graph = graph
        self.package = package
        self.run_id = run_id

    def runtime_step_cli(
        self, cli_args, retry_count, max_user_code_retries, ubf_context
    ):
        # No-op when already inside the container
        if getattr(self, "_inside_container", False):
            return

        if retry_count <= max_user_code_retries:
            # Redirect execution from local -> devcontainer sandbox
            cli_args.commands = ["devcontainer", "step"]

            # Capture flow file path for mounting into container
            flow_file = None
            for part in cli_args.entrypoint:
                if not part.startswith("-"):
                    if part != sys.executable and not part.endswith("python3"):
                        flow_file = os.path.abspath(part)
            if flow_file:
                cli_args.command_options["flow-file"] = flow_file

            # Capture top-level options for reconstruction
            top = cli_args.top_level_options
            cli_args.command_options["datastore-type"] = top.get("datastore", "local")
            cli_args.command_options["metadata-type"] = top.get("metadata", "local")

            # Pass decorator attributes as CLI options
            for k, v in self.attributes.items():
                if k == "packages" and v:
                    cli_args.command_options[k] = json.dumps(v)
                else:
                    cli_args.command_options[k] = v

            cli_args.entrypoint[0] = sys.executable

    def task_pre_step(
        self,
        step_name,
        task_datastore,
        metadata,
        run_id,
        task_id,
        flow,
        graph,
        retry_count,
        max_retries,
        ubf_context,
        inputs,
    ):
        if os.environ.get("METAFLOW_DEVCONTAINER"):
            from metaflow.metadata_provider import MetaDatum
            meta = {
                "devcontainer-image": os.environ.get(
                    "METAFLOW_DEVCONTAINER_IMAGE", ""
                ),
                "devcontainer-backend": os.environ.get(
                    "METAFLOW_DEVCONTAINER_BACKEND", "devcontainer"
                ),
            }
            entries = [
                MetaDatum(
                    field=k, value=v, type=k,
                    tags=["attempt_id:%d" % retry_count],
                )
                for k, v in meta.items()
            ]
            metadata.register_metadata(run_id, step_name, task_id, entries)
