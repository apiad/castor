import importlib
import sys
from typing import Optional

import typer

from .core import Manager
from .server import Server
from .ui import CastorUI

app = typer.Typer()


@app.command()
def run(
    path: str = typer.Argument(
        ...,
        help="The import path to the Manager instance, e.g., 'my_app.main:manager'",
    ),
    workers: int = typer.Option(
        4, "--workers", "-w", help="Number of processes for CPU-bound tasks."
    ),
    threads: int = typer.Option(
        8, "--threads", "-t", help="Number of threads for I/O-bound tasks."
    ),
    interactive: bool = typer.Option(
        False, '--interactive', '-i', help="Run an interactive display."
    )
):
    """
    Starts a Castor worker to process background tasks.
    """
    try:
        module_path, variable_name = path.split(":", 1)
    except ValueError:
        print(f"Error: Invalid path '{path}'. Must be in 'module.path:variable' format.")
        raise typer.Exit(code=1)

    try:
        module = importlib.import_module(module_path)
    except ImportError:
        print(f"Error: Could not import module '{module_path}'.")
        raise typer.Exit(code=1)

    try:
        manager = getattr(module, variable_name)
    except AttributeError:
        print(f"Error: Variable '{variable_name}' not found in module '{module_path}'.")
        raise typer.Exit(code=1)

    if not isinstance(manager, Manager):
        print(f"Error: The variable '{path}' is not an instance of a Castor Manager.")
        raise typer.Exit(code=1)

    server = Server(manager=manager, workers=workers, threads=threads, manager_path=path)

    if interactive:
        ui = CastorUI(manager, server)
        ui.run()
    else:
        try:
            print("Starting server... Ctrl+C to stop.")
            server.serve()
        except KeyboardInterrupt:
            print("\nStoping server...")
            server.stop()
            print("Done.")


if __name__ == "__main__":
    app()
