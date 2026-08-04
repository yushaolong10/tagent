"""CLI entry point for the s20 coding agent."""
from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path


def existing_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(
            f"work directory does not exist or is not a directory: {value}")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the s20 coding agent")
    parser.add_argument(
        "-C", "--workdir", type=existing_directory, default=Path.cwd(),
        help="workspace directory used by the agent (default: current directory)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # config.py captures Path.cwd() during import, so switch directories before
    # importing any project module that derives workspace paths from it.
    os.chdir(args.workdir)

    import config
    from skills import scan_skills
    from cron import cron_scheduler_loop, load_durable_jobs
    from agent import (agent_loop, cron_autorun_loop, print_turn_assistants,
                       agent_lock, update_context)
    from tools import trigger_hooks
    from working_state import WorkingState

    config.CLI_ACTIVE = True
    scan_skills()
    load_durable_jobs()
    threading.Thread(target=cron_scheduler_loop, daemon=True).start()

    print(f"a comprehensive agent ({config.WORKDIR})")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    history = []
    working_state = WorkingState()

    # One shared context dict for the whole session: cron_autorun_loop and the
    # main loop both update it in place, so neither rebinds the reference.
    context = update_context({}, [])
    threading.Thread(target=cron_autorun_loop,
                     args=(history, context, working_state), daemon=True).start()
    while True:
        try:
            query = input(config.PROMPT)
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", query)
        with agent_lock:
            # A user turn and a scheduled turn share history and state.  Set
            # both only after taking the same lock used by cron execution.
            working_state.start_turn(query)
            turn_start = len(history)
            history.append({"role": "user", "content": query})
            agent_loop(history, context, working_state)
            context.update(update_context(context, history))
            print_turn_assistants(history, turn_start)
        print()


if __name__ == "__main__":
    main()
