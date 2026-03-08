# @devcontainer Decorator — Documentation

Run Metaflow steps in isolated, **spec-compliant** [Development Containers](https://containers.dev/).
The same `devcontainer.json` used for local development in VS Code also powers sandboxed execution.

## Quick Start

```python
from metaflow import FlowSpec, step

class TrainFlow(FlowSpec):

    @step
    def start(self):
        from metaflow import devcontainer        # decorator auto-registered

        self.message = "Hello from sandbox!"
        self.next(self.train)

    @devcontainer(image="python:3.11")
    @step
    def train(self):
        import platform
        print("Running on:", platform.node())
        self.result = 42
        self.next(self.end)

    @step
    def end(self):
        print("Result from sandbox:", self.result)

if __name__ == "__main__":
    TrainFlow()
```

Run it:

```bash
python train_flow.py run
```

Or apply the decorator from the CLI without modifying your code:

```bash
python train_flow.py run --with devcontainer:image=python:3.11
```

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `image` | str | `python:X.Y` | Container image. Defaults to matching your interpreter. |
| `packages` | dict | `{}` | Python packages to install. `{"numpy": ">=1.24"}` |
| `python` | str | None | Python version for the container image. |
| `network` | bool | `True` | Whether the container has network access. |
| `memory` | str | None | Memory limit, e.g. `"4g"`. |
| `cpu` | int | None | CPU count limit. |
| `backend` | str | `"devcontainer"` | Execution backend: `devcontainer`, `devpod`, or `daytona`. |
| `security` | str | `"none"` | Security level: `none`, `standard`, or `strict`. |
| `devcontainer_json` | str | None | Path to a custom `devcontainer.json` file. |

---

## Backends

The decorator supports three execution backends that all consume the same `devcontainer.json` spec:

```python
# Default: @devcontainers/cli
@devcontainer(image="python:3.11")

# DevPod (remote workspaces)
@devcontainer(image="python:3.11", backend="devpod")

# Daytona (managed environments)
@devcontainer(image="python:3.11", backend="daytona")
```

| Backend | CLI | `up` command | `exec` command | `down` command |
|---|---|---|---|---|
| `devcontainer` | `@devcontainers/cli` | `devcontainer up` | `devcontainer exec` | `docker rm -f` |
| `devpod` | DevPod | `devpod up` | `devpod ssh --command` | `devpod delete` |
| `daytona` | Daytona | `daytona create` | `daytona exec` | `daytona delete` |

### Installation

```bash
# devcontainer CLI (default)
npm install -g @devcontainers/cli

# DevPod
curl -L -o devpod "https://github.com/loft-sh/devpod/releases/latest/download/devpod-linux-amd64"
sudo install -c -m 0755 devpod /usr/local/bin

# Daytona
curl -sf -L https://download.daytona.io/daytona/install.sh | sudo bash
```

---

## Security Policies

Three escalating sandbox isolation levels via Docker security primitives:

```python
# No restrictions (default)
@devcontainer(image="python:3.11", security="none")

# Standard: drop capabilities, no privilege escalation, no network
@devcontainer(image="python:3.11", security="standard")

# Strict: standard + read-only rootfs, PID limit, seccomp
@devcontainer(image="python:3.11", security="strict")
```

| Level | Capabilities | Privilege Escalation | Network | Filesystem | PID Limit |
|---|---|---|---|---|---|
| `none` | All | Allowed | ✅ On | Read-Write | Unlimited |
| `standard` | Dropped | Blocked | ❌ Off | Read-Write | Unlimited |
| `strict` | Dropped | Blocked | ❌ Off | Read-Only | 4096 |

---

## Using a Custom devcontainer.json

Point to your own spec-compliant configuration:

```python
@devcontainer(devcontainer_json=".devcontainer/devcontainer.json")
@step
def train(self):
    ...
```

Your `devcontainer.json` must contain either `"image"` or `"build"`. The decorator will
automatically add the Metaflow datastore mount and environment variables if they're not
already defined.

---

## How It Works

```
Host Runtime                      devcontainer CLI                  Container
───────────                       ────────────────                  ─────────
@devcontainer @step
def train(self): ...
        │
        │  runtime_step_cli()
        ▼
DevcontainerDecorator
  1. Generate devcontainer.json ──► .devcontainer/devcontainer.json
  2. backend.up() ──────────────► Builds image, starts container
  3. backend.exec() ────────────► python flow.py step train ...
        │                                              │
        │                         Mount ~/.metaflow ◄──┘
        ▼                         (bind mount)
  4. chown fix + cleanup
```

1. **`step_init()`** validates the backend CLI, Docker daemon, and decorator conflicts.
2. **`runtime_step_cli()`** intercepts the Metaflow runtime and redirects execution to the `devcontainer step` CLI command.
3. The CLI generates a `devcontainer.json`, calls `backend.up()` to start the container, then `backend.exec()` to run the step inside it.
4. After execution, a `chown` wrapper corrects file ownership so the host can read the container's output.

---

## Requirements

- **Docker** (running daemon)
- One of: `devcontainer` CLI, `devpod`, or `daytona`
- Python 3.7+
- Metaflow 2.x
