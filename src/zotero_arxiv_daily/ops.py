# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Explicit operator recovery for SMTP batches; never runs on a schedule."""
import argparse
import json
import os
import dotenv
from .state import State


def main():
    dotenv.load_dotenv()
    parser = argparse.ArgumentParser(description="Inspect or release uncertain email batches")
    parser.add_argument("command", choices=["batches", "release"])
    parser.add_argument("--batch")
    parser.add_argument("--confirm-not-delivered", action="store_true")
    args = parser.parse_args()
    if args.command == "release" and (not args.batch or not args.confirm_not_delivered):
        parser.error("release requires --batch and --confirm-not-delivered after checking delivery")
    state = State(os.environ.get("FEEDBACK_BASE_URL", ""), os.environ.get("FEEDBACK_API_TOKEN", ""))
    if args.command == "batches":
        print(json.dumps(state.call("batches"), ensure_ascii=False, indent=2))
    else:
        state.call("recover", id=args.batch, confirm_not_delivered=True)
        print("Batch released; old operation links revoked. Still-pending papers can be recommended again.")


if __name__ == "__main__":
    main()
