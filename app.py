from main import create_api_info_panel
import uvicorn
import os
from rich.console import Console

SERVER_HOST = os.getenv("HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PORT", 8000))
console = Console(stderr=True)

console.print(create_api_info_panel(), style="bold")
console.print("[bold]--- Server Logs Start Below ---[/]", style="dim")

uvicorn.run(
    "main:app",
    host=SERVER_HOST,
    port=SERVER_PORT,
    log_level="warning",
    access_log=False,
    ws_ping_interval=25,
    ws_ping_timeout=20,
)