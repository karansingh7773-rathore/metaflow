# GSoC 2026 Proposal: Sandboxed Execution Environments with Devcontainers

**Organization:** Metaflow (Netflix / Outerbounds)
**Project:** Sandboxed Execution Environments with Devcontainers
**Difficulty:** Medium | **Duration:** 175 hours
**Mentors:** Romain, Savin
**Applicant:** Karan Singh Rathore

---

## Abstract

Metaflow steps can run in containers via `@kubernetes` or `@batch`, but these require cloud infrastructure. For **local development and CI environments**, there is no built-in way to run steps in isolated, reproducible sandboxes. This proposal introduces a `@devcontainer` step decorator that leverages the [Development Container Specification](https://containers.dev/) to execute Metaflow steps inside sandboxed environments — using the same `devcontainer.json` that powers VS Code, Codespaces, DevPod, and Daytona.

**Key Insight:** Instead of building yet another Docker wrapper, we build on an open standard (`devcontainer.json`) that already has a rich ecosystem. The same config file that defines a development environment also defines an execution sandbox.

---

## 1. Problem Statement

### The Gap

| Execution Mode | Infrastructure | Isolation | Local Dev | CI Friendly |
|---|---|---|---|---|
| Local (default) | None | ❌ | ✅ | ✅ |
| `@kubernetes` | K8s cluster | ✅ | ❌ | ❌ |
| `@batch` | AWS Batch | ✅ | ❌ | ❌ |
| **`@devcontainer`** | **Docker only** | **✅** | **✅** | **✅** |

Developers need isolation for:
- **Reproducibility:** "It works on my machine" — now provably, via container specs.
- **Dependency isolation:** Run steps with different Python versions or conflicting libraries without polluting the host.
- **Security:** Untrusted user code (e.g., in multi-tenant CI) should run with dropped capabilities and limited privileges.
- **CI/CD pipelines:** GitHub Actions, GitLab CI already have Docker — no Kubernetes needed.

### Why `devcontainer.json`, Not `docker run`?

| Capability | Raw `docker run` | `devcontainer` spec |
|---|---|---|
| Spec-compliant | ❌ | ✅ Standardized JSON |
| VS Code compatible | ❌ | ✅ Same config |
| Features ecosystem | ❌ Manual | ✅ `ghcr.io/devcontainers/features/*` |
| DevPod / Daytona | ❌ | ✅ Both consume devcontainer.json |
| Lifecycle hooks | ❌ | ✅ `postCreateCommand`, etc. |
| Portable | ❌ Docker-specific | ✅ Backend-agnostic |

---

## 2. Implementation — Working Prototype

> **I have already built a working prototype.** The following describes the architecture of the implemented system, not a theoretical plan.

### Architecture

```
Host Runtime                      Backend                          Container
───────────                       ───────                          ─────────
@devcontainer @step
def train(self): ...
        │
        │  runtime_step_cli()     ← intercepts execution
        ▼
DevcontainerDecorator
  1. Preflight validation         ← CLI + Docker checks
  2. Generate devcontainer.json   ← spec-compliant config
  3. backend.up()  ──────────────► Starts container
  4. backend.exec() ─────────────► python flow.py step train
        │                                              │
        │                         ~/.metaflow ◄────────┘
        ▼                         (bind mount)
  5. chown fix + backend.down()
```

### Files

| File | Lines | Purpose |
|---|---|---|
| `devcontainer_decorator.py` | ~190 | `StepDecorator`: `step_init` (preflight), `runtime_step_cli` (interception), `task_pre_step` (metadata) |
| `devcontainer_config.py` | ~250 | Spec-compliant `devcontainer.json` generator with security policies and env passthrough |
| `devcontainer_cli.py` | ~200 | CLI engine: orchestrates `up` → `exec` → `down` lifecycle |
| `backends.py` | ~350 | `ContainerBackend` abstraction: DevcontainerBackend, DevPodBackend, DaytonaBackend |
| `plugins/__init__.py` | +3 | Plugin registration |

### Core Mechanism: `runtime_step_cli()`

This is the critical hook that intercepts Metaflow's normal execution path. When the runtime is about to execute a step locally, the decorator redirects it through the devcontainer subprocess:

```python
def runtime_step_cli(self, cli_args, retry_count, max_user_code_retries, ubf_context):
    if getattr(self, "_inside_container", False):
        return  # No-op inside container — prevents infinite recursion

    if retry_count <= max_user_code_retries:
        # Redirect: python flow.py step → python flow.py devcontainer step
        cli_args.commands = ["devcontainer", "step"]

        # Pass flow file path and decorator attributes
        cli_args.command_options["flow-file"] = flow_file
        cli_args.command_options.update(self.attributes)
```

---

## 3. Production-Level Challenges Solved

### Challenge 1: Infinite Recursion Prevention

**Problem:** The `@devcontainer` decorator is applied to the step code. When the container executes the step, the decorator is still present → spawns another container → infinite loop.

**Solution:** Set `METAFLOW_DEVCONTAINER=1` as an env var inside the container. In `step_init()`, detect this and make the decorator a no-op:

```python
if os.environ.get("METAFLOW_DEVCONTAINER"):
    self._inside_container = True
    return
```

### Challenge 2: Root User File Permissions

**Problem:** Containers typically run as `root`. Files written to the bind-mounted `~/.metaflow` datastore are owned by `root:root` with `0600` permissions. When the host runtime (running as the user) tries to read the results, it crashes with `Permission denied`.

**Solution:** Wrap the step command in a bash script that runs `chown -R` after the step finishes but before the container exits:

```python
fix_perms = "chown -R %s:%s /tmp/metaflow/*/%s /tmp/metaflow/*/data" % (uid, gid, run_id)
wrapped_cli = ["bash", "-c", "%s ; err=$? ; %s ; exit $err" % (step_cli, fix_perms)]
```

### Challenge 3: `Unknown step decorator` Error

**Problem:** When using `--with devcontainer:image=python:3.11`, the `devcontainer` decorator name is passed to the step command inside the container. But the container's pip-installed Metaflow doesn't have the devcontainer plugin registered, causing an "Unknown step decorator" error.

**Solution:** Strip `devcontainer` from the `--with`/`decospecs` parameter when reconstructing the CLI command for the container:

```python
if "decospecs" in top_params and top_params["decospecs"]:
    top_params["decospecs"] = tuple(
        spec for spec in top_params["decospecs"]
        if not spec.startswith("devcontainer")
    )
```

### Challenge 4: Non-Root Container Images

**Problem:** Many devcontainer images (e.g., `mcr.microsoft.com/devcontainers/python`) use non-root users like `vscode`. Mounting to `/root/.metaflow` fails silently.

**Solution:** Mount to `/tmp/metaflow` (world-writable) and set `METAFLOW_DATASTORE_SYSROOT_LOCAL` accordingly.

### Challenge 5: Environment Variable Context

**Problem:** Metaflow relies on many `METAFLOW_*` env vars plus identity vars (`USER`, `HOME`) for correct operation. The container doesn't inherit these.

**Solution:** Auto-forward all `METAFLOW_*` host env vars, plus `USER`, `USERNAME`, `HOME`, `LOGNAME` into the container's `containerEnv`. To ensure full environment parity, the generator will also pass-through host-level credentials (e.g., `AWS_*` or `AZURE_*` env vars) to allow the sandboxed step to interact with remote metadata services if configured.

---

## 4. Multi-Backend Support

The decorator supports three backends via a clean `ContainerBackend` abstraction:

```python
class ContainerBackend:
    def up(self, workspace_dir, config_path):   # Start container
    def exec(self, workspace_id, cmd):          # Run command
    def down(self, workspace_id):               # Tear down
```

| Backend | Command | Use Case |
|---|---|---|
| `DevcontainerBackend` | `@devcontainers/cli` | Local development, CI |
| `DevPodBackend` | DevPod | Remote workspaces, cloud-agnostic |
| `DaytonaBackend` | Daytona | Managed enterprise environments |

All three consume the **same `devcontainer.json`** — users switch backends with a single parameter:

```python
@devcontainer(image="python:3.11", backend="devpod")
```

---

## 5. Security Policies

Three escalating sandbox isolation levels via Docker security primitives:

| Level | Capabilities | Privilege Escalation | Network | Filesystem | PID Limit |
|---|---|---|---|---|---|
| `none` | All | Allowed | On | Read-Write | Unlimited |
| `standard` | `--cap-drop=ALL` | `--no-new-privileges` | `--network=none` | R/W | Unlimited |
| `strict` | Dropped | Blocked | Off | `--read-only` | 4096 |

The `strict` level also includes a `--pids-limit=4096` to prevent fork-bomb style attacks within the local executor.

Usage:
```python
@devcontainer(image="python:3.11", security="strict")
@step
def untrusted_step(self):
    ...
```

---

## 6. Verified E2E Flow Execution

The following flow was executed successfully on a remote Ubuntu 22.04 VM:

```python
class DevcontainerRealFlow(FlowSpec):
    @devcontainer(image="python:3.11")
    @step
    def start(self):
        import platform
        self.sandboxed = True
        self.hostname = platform.node()
        self.message = "Hello from devcontainer sandbox!"
        self.next(self.end)

    @step
    def end(self):
        assert self.sandboxed is True        # ✅ Data persisted
        assert self.message == "Hello from devcontainer sandbox!"   # ✅ Artifacts readable
        print("Flow completed successfully!")
```

**Results:**
- ✅ `start` step ran inside `python:3.11` container
- ✅ Data artifacts written via bind-mounted local datastore
- ✅ File permissions corrected by `chown` wrapper
- ✅ `end` step (host) successfully read containerized step's artifacts
- ✅ Container cleaned up after execution

---

## 7. Timeline

| Week | Task | Deliverable |
|---|---|---|
| 1–2 | Core decorator + config generator | `@devcontainer` with `step_init`, `runtime_step_cli` |
| 3–4 | CLI engine + E2E testing | `devcontainer step` command, E2E test passing |
| 5–6 | Backend abstraction + DevPod/Daytona | Multi-backend support, `backends.py` |
| 7–8 | Security policies | `security` parameter with three levels |
| 9–10 | Custom `devcontainer.json` passthrough | Full spec support, lifecycle hooks |
| 11–12 | Testing, CI integration, docs | Test suite, CI examples, final documentation |
| 13 | Buffer + final review | Code review, merge preparation |

> **Note:** The core implementation (weeks 1–8) is already complete. The remaining time will focus on polishing, testing edge cases, CI integration examples, and community review.

---

## 8. About Me

**Karan Singh Rathore**
GitHub: [@karansingh7773-rathore](https://github.com/karansingh7773-rathore)

### Contributions to Netflix/metaflow (4 PRs)

| PR | Type | Description |
|---|---|---|
| [**#2117** — Add support for Parameters of type enum](https://github.com/Netflix/metaflow/pull/2117) | Feature | Added `enum` type support to Metaflow's `Parameter` system. +237/-2 lines, 17 comments of code review. |
| [**#2908** — Fix `decompress_list` crashes on multi-colon input paths](https://github.com/Netflix/metaflow/pull/2908) | Bug fix | Fixed a `ValueError` in the path decompression utility that affected Airflow and Argo integrations. Included E2E tests with chaos testing. |
| [**#2873** — Raise clear error when `@timeout` is used on Windows](https://github.com/Netflix/metaflow/pull/2873) | Bug fix | Replaced a cryptic `signal.SIGALRM` crash with a descriptive `MetaflowException` for Windows users. |
| [**#2791** — Prevent packaging exclusion of files under hidden root ancestors](https://github.com/Netflix/metaflow/pull/2791) | Bug fix | Fixed silent file exclusion in `metaflow/packaging_sys/utils.py` when the flow path contained a hidden directory ancestor. |

### Why This Project

I chose this project because I believe in bridging the gap between **local development** and **production execution**. The devcontainer spec is the right abstraction — it's already battle-tested in VS Code, Codespaces, and DevPod. Bringing it to Metaflow means data scientists get reproducible, isolated environments without needing a Kubernetes cluster.

My contributions above demonstrate:
- **Feature development:** Building a new capability end-to-end (#2117)
- **Deep codebase knowledge:** Debugging edge cases in utilities, packaging, and decorators (#2908, #2791, #2873)
- **Production thinking:** Every PR includes proper error handling and tests

My experience in resolving the multi-colon path crash in `util.py` (PR #2908) gave me deep insight into Metaflow's internal CLI routing, which I have directly applied to the `@devcontainer` interception logic.

### Technical Skills

Python, Docker, Linux, CI/CD, TypeScript, Rust, Git workflows, code review

---

## 9. References

- [Development Container Specification](https://containers.dev/)
- [Metaflow `@kubernetes` decorator](https://docs.metaflow.org/scaling/remote-tasks/kubernetes) — Pattern reference
- [DevPod Documentation](https://devpod.sh/docs/)
- [Daytona Documentation](https://www.daytona.io/docs/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
