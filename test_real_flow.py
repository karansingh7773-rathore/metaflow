"""
Real Metaflow flow to test @devcontainer decorator.
The 'start' step runs inside a devcontainer sandbox.
Run with: python test_real_flow.py run
"""
from metaflow import FlowSpec, step


class DevcontainerRealFlow(FlowSpec):

    @step
    def start(self):
        """This step runs inside the devcontainer sandbox."""
        import platform
        import os

        self.python_version = platform.python_version()
        self.hostname = platform.node()
        self.is_sandboxed = os.environ.get("METAFLOW_DEVCONTAINER", "0") == "1"
        self.message = "Hello from devcontainer sandbox!"

        print("=== Step running ===")
        print("Python: %s" % self.python_version)
        print("Hostname: %s" % self.hostname)
        print("Sandboxed: %s" % self.is_sandboxed)
        print("Message: %s" % self.message)

        self.next(self.end)

    @step
    def end(self):
        """This step runs locally, verifying data persists from sandbox."""
        print("=== Data from sandboxed step ===")
        print("Python version was: %s" % self.python_version)
        print("Hostname was: %s" % self.hostname)
        print("Was sandboxed: %s" % self.is_sandboxed)
        print("Message: %s" % self.message)
        print("=== Flow completed successfully! ===")


if __name__ == "__main__":
    DevcontainerRealFlow()
