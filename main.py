
import argparse
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

# Load the API key before importing the agent.
PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

if not os.getenv("GROQ_API_KEY"):
    print("ERROR: GROQ_API_KEY was not found in your .env file.")
    sys.exit(1)

# Import only after the environment variables are loaded.
from agent.graph import agent


def main():
    parser = argparse.ArgumentParser(
        description="Run engineering project planner"
    )
    parser.add_argument(
        "--recursion-limit",
        "-r",
        type=int,
        default=100,
        help="Recursion limit for processing (default: 100)",
    )

    args = parser.parse_args()

    try:
        user_prompt = input("Enter your project prompt: ").strip()

        if not user_prompt:
            print("Please enter a project prompt.")
            sys.exit(1)

        agent.invoke(
            {"user_prompt": user_prompt},
            {"recursion_limit": args.recursion_limit},
        )

        print("\nProject generation complete.")
        print(f"Generated files are in: {PROJECT_DIR / 'generated_project'}")

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)

    except Exception as e:
        traceback.print_exc()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
