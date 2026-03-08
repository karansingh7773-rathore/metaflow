"""
Test flow to verify the @devcontainer decorator.
Run with: python test_devcontainer_flow.py run
"""
from metaflow import FlowSpec, step
from metaflow.plugins.devcontainer.devcontainer_decorator import DevcontainerDecorator


class DevcontainerTestFlow(FlowSpec):

    @DevcontainerDecorator(attributes={"image": "python:3.11"})
    @step
    def start(self):
        import platform
        self.python_version = platform.python_version()
        self.message = "Hello from devcontainer!"
        print("Running inside devcontainer sandbox")
        print("Python version: %s" % self.python_version)
        self.next(self.end)

    @step
    def end(self):
        print("Flow completed!")
        print("Message from start: %s" % self.message)


if __name__ == "__main__":
    DevcontainerTestFlow()
