# castor/ui.py
import threading
import time
from collections import deque
from datetime import datetime
from uuid import uuid4

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from .core import Manager
from .server import Server


class CastorUI:
    def __init__(self, manager: Manager, server: Server):
        self._manager = manager
        self._server = server
        self._console = Console()
        self._logs = deque(maxlen=20)  # Store the last 20 log messages

    def _log_listener(self):
        """Listens for log messages from the pub/sub topic and adds them to our deque."""
        with self._server._log_channel.subscribe() as listener:
            for msg in listener.listen():
                self._logs.append(msg)

    def run(self):
        """Starts the server thread and the rich UI."""
        server_thread = threading.Thread(target=self._server.serve, daemon=True)
        server_thread.start()

        log_thread = threading.Thread(target=self._log_listener, daemon=True)
        log_thread.start()

        layout = self._make_layout()

        while True:
            try:
                with Live(layout, console=self._console, screen=True) as live:
                    while not self._server._shutdown_event.is_set():
                        # Refresh the display periodically
                        live.update(self._make_layout())
                        time.sleep(0.5)
            except KeyboardInterrupt:
                self._server.stop()
                self._console.clear()
                break

        # Wait for the server to finish shutting down
        server_thread.join()

    def _make_layout(self) -> Layout:
        """Creates the rich layout for the UI."""
        layout = Layout(name="root")

        layout.split(
            Layout(name="header", size=3),
            Layout(ratio=1, name="main"),
            Layout(size=3, name="footer"),
        )

        layout["main"].split_row(Layout(name="side"), Layout(name="body", ratio=3))

        layout["header"].update(self._make_header())
        layout["side"].update(self._make_status_panel())
        layout["body"].update(self._make_log_panel())
        layout["footer"].update(self._make_footer())

        return layout

    def _make_header(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(
            "[b]Castor Worker Dashboard[/b]",
            datetime.now().ctime().replace(":", "[blink]:[/]"),
        )
        return Panel(grid, style="white on blue")

    def _make_footer(self) -> Panel:
        return Panel("[green]Listening for tasks...[/] [bold](Ctrl+C to exit)[/]", style="white on black")

    def _make_status_panel(self) -> Panel:
        """Creates the panel showing the number of pending tasks."""
        pending_count = len(self._manager._pending_tasks)
        status_text = f"[bold cyan]{pending_count}[/] tasks pending"
        return Panel(status_text, title="[bold]Status[/]", border_style="green")

    def _make_log_panel(self) -> Panel:
        """Creates the panel displaying the latest log messages."""
        log_table = Table(show_header=False, box=None, expand=True)
        log_table.add_column("Level", width=6)
        log_table.add_column("Task ID", width=len(str(uuid4())))
        log_table.add_column("Task Name", width=12)
        log_table.add_column("Message")

        for log in self._logs:
            level_style = "red" if log.level == "error" else "green"
            log_table.add_row(f"[{level_style}]{log.level}[/]", log.id or "-", log.task or "-", log.message)

        return Panel(log_table, title="[bold]Logs[/]", border_style="magenta")
