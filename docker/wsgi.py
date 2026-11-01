from pybossa.core import create_app

# Scheduling is initialized once by the init container, not once per web worker.
app = create_app(run_as_server=False)
