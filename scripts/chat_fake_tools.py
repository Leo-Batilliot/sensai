"""`sensai chat` with stand-in tools, to try the tool loop (CORE-06) and the system
prompt (CORE-08) on the real demo database, before the MCP registry exists (MCP-04).

Tools: time__get_current_time (the computer's clock) and sql_toolbox__run_select,
read-only on [database] business_path. Generate the database first:

    .venv\\Scripts\\python -m sensai seed
    .venv\\Scripts\\python scripts\\chat_fake_tools.py
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fakes import FakeToolRegistry, select_tool, time_tool  # noqa: E402
from sensai.cli.app import ChatSession, run_interruptible, run_repl  # noqa: E402
from sensai.cli.render import Renderer  # noqa: E402
from sensai.core.config import load_config  # noqa: E402
from sensai.core.engine import Engine  # noqa: E402
from sensai.core.prompts import system_prompt  # noqa: E402


def main() -> int:
    config = load_config()
    db_path = config.database.business_path
    if not db_path.is_file():
        print(f"Base absente : {db_path}. Lancez d'abord : python -m sensai seed", file=sys.stderr)
        return 1
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    registry = FakeToolRegistry(time_tool(now=now), select_tool(db_path=db_path))
    prompt = system_prompt(db_path, [tool.name for tool in registry.tools()])

    renderer = Renderer()
    engine = Engine(config, registry=registry, system_prompt=prompt)
    session = ChatSession(engine, renderer)
    loop = asyncio.new_event_loop()
    try:
        renderer.hint(f"Chat avec outils de test sur {db_path} · /exit pour quitter")
        if not run_interruptible(loop, session.check_model()):
            return 1
        run_repl(session, loop)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        loop.run_until_complete(engine.aclose())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    sys.exit(main())
