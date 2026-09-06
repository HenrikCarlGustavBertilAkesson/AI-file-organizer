from agents.organizer import run_agent
from actions.validator import validate_action
from actions.executor import execute_action
from pathlib import Path

def main():
    allowed_root = input(
        "Enter the directory the agent may organize:\n> "
    )

    request = input(
        "\nWhat would you like the agent to do?\n> "
    )

    allowed_root = str(
        Path(allowed_root)
        .expanduser()
        .resolve()
    )

    agent_request = f"""
        Allowed organization directory:

        {allowed_root}

        User request:

        {request}
        """

    result = run_agent(agent_request)


    print("\n--- AGENT RESULT ---\n")
    print(result.message)

    if not result.proposed_actions:
        print("\nNo file actions proposed.")
        return

    print("\n--- PROPOSED ACTIONS ---\n")

    for index, action in enumerate(
        result.proposed_actions,
        start=1,
    ):
        print(f"{index}. MOVE")

        print(f"   From: {action.source}")
        print(f"   To:   {action.destination}")
        print(f"   Why:  {action.reason}")

        valid, error = validate_action(
            action,
            allowed_root,
        )

        if not valid:
            print(f"   INVALID: {error}")
            continue

        answer = input(
            "\nApprove this move? [y/N]: "
        )

        if answer.lower() != "y":
            print("Rejected.")
            continue

        action.approved = True

        success = execute_action(
            action,
            allowed_root,
        )

        if success:
            print("Moved successfully.")
        else:
            print(
                f"Move failed: {action.error}"
            )


if __name__ == "__main__":
    main()