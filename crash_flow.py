from metaflow import FlowSpec, step

class MultiColonCrashFlow(FlowSpec):
    @step
    def start(self):
        self.next(self.end)

    @step
    def end(self):
        print("SUCCESS: The multi-colon bug is fixed and the flow completed!")

if __name__ == '__main__':
    MultiColonCrashFlow()
