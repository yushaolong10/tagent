"""CLI entry point for the s20 coding agent."""
from __future__ import annotations

import threading

import config
from skills import scan_skills
from cron import cron_scheduler_loop, load_durable_jobs
from agent import (agent_loop, cron_autorun_loop, print_turn_assistants,
                   agent_lock, update_context)
from tools import trigger_hooks
from teammates import consume_lead_inbox
from working_state import WorkingState


def main() -> None:
    config.CLI_ACTIVE = True
    scan_skills()
    load_durable_jobs()
    threading.Thread(target=cron_scheduler_loop, daemon=True).start()

    print("s20: comprehensive agent")
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
            # History is owned by the serialized agent runner.  Teammate
            # messages are appended while holding the same lock as cron work.
            inbox = consume_lead_inbox(route_protocol=True)
            if inbox:
                def inbox_label(msg):
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    suffix = f" req:{req_id}" if req_id else ""
                    return f"{msg.get('type', 'message')}{suffix}"

                inbox_text = "\n".join(
                    f"From {m['from']} [{inbox_label(m)}]: "
                    f"{m['content'][:200]}" for m in inbox)
                history.append({"role": "user",
                                "content": f"[Inbox]\n{inbox_text}"})
        print()


if __name__ == "__main__":
    main()
