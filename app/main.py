from pathlib import Path

from agents.organizer import run_agent
from database import create_database, save_action
from review import review_pending_actions


def main():
    create_database()

    allowed_root_input = input(
        "Enter the directory the agent may organize:\n> "
    )

    allowed_root = str(
        Path(allowed_root_input)
        .expanduser()
        .resolve()
    )

    request = input(
        "\nWhat would you like the agent to do?\n> "
    )

    agent_request = f"""
        Allowed organization directory:

        {allowed_root}

        User request:

        {request}
        """

    result = run_agent(agent_request, allowed_root=allowed_root)

    print("\n--- AGENT RESULT ---\n")
    print(result.message)
    print(f'AI usage: {result.usage}')

    for action in result.proposed_actions:
        save_action(action)

    print(
        f"\nSaved "
        f"{len(result.proposed_actions)} "
        f"proposed actions."
    )

    review_pending_actions(
        allowed_root
    )


if __name__ == "__main__":
    main()
