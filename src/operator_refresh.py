import argparse
import subprocess
import sys


SUBPROCESS_TIMEOUT_SECONDS = 120

STEPS = [
    {
        "name": "opportunity state",
        "skip_arg": "skip_state",
        "command": [sys.executable, "src/opportunity_state.py"],
        "outputs": ["data/opportunity_state.csv"],
    },
    {
        "name": "triage board",
        "skip_arg": "skip_triage_board",
        "command": [sys.executable, "src/triage_board.py"],
        "outputs": ["reports/triage/govcon_triage_board.md"],
    },
    {
        "name": "triage review pack",
        "skip_arg": "skip_review_pack",
        "command": [sys.executable, "src/triage_review_pack.py"],
        "outputs": ["reports/triage/govcon_triage_review_pack.md"],
    },
    {
        "name": "finalist action board",
        "skip_arg": "skip_action_board",
        "command": [sys.executable, "src/finalist_action_board.py"],
        "outputs": ["reports/triage/finalist_action_board.md"],
    },
]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Refresh local GovCon Scout operating outputs without calling SAM.gov, "
            "USAspending, or document downloaders."
        )
    )
    parser.add_argument("--skip-state", action="store_true")
    parser.add_argument("--skip-triage-board", action="store_true")
    parser.add_argument("--skip-review-pack", action="store_true")
    parser.add_argument("--skip-action-board", action="store_true")
    return parser.parse_args()


def run_step(step):
    print("")
    print(f"Refreshing {step['name']}...", flush=True)
    print(" ".join(step["command"]), flush=True)
    try:
        result = subprocess.run(step["command"], timeout=SUBPROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print("")
        print(f"Refresh timed out during {step['name']} after {SUBPROCESS_TIMEOUT_SECONDS} seconds.")
        return 124
    if result.returncode != 0:
        print("")
        print(f"Refresh failed during {step['name']} with exit code {result.returncode}.")
        return result.returncode

    for output in step["outputs"]:
        print(f"- {output}")
    return 0


def main():
    args = parse_args()
    completed = []

    for step in STEPS:
        if getattr(args, step["skip_arg"]):
            print(f"Skipping {step['name']}.")
            continue

        return_code = run_step(step)
        if return_code != 0:
            sys.exit(return_code)
        completed.extend(step["outputs"])

    print("")
    print("Local operator refresh complete.")
    print("Outputs:")
    for output in completed:
        print(f"- {output}")


if __name__ == "__main__":
    main()
