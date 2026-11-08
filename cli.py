"""`hermes openloft-memory …` subcommands.

Discovered by ``discover_plugin_cli_commands()`` and shown only while this
provider is the active ``memory.provider``. Keys are resolved through the
Hermes secret scope — never a raw environment read (catalog rule 11).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _agent_id() -> str:
    from .scope import resolve_agent_id
    return resolve_agent_id(
        profile_identity=os.environ.get("HERMES_PROFILE", ""),
        fallback=os.environ.get("AGENT_ID") or "hermes",
    )


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _backend():
    """Build a configured client or exit with an actionable message."""
    from agent.secret_scope import get_secret

    key = get_secret("OPENLOFT_MEMORY_API_KEY")
    if not key:
        sys.exit("No OPENLOFT_MEMORY_API_KEY configured — run `hermes memory setup` "
                 "or mint a key at the dashboard (Memory → Connections).")

    from .client import ServiceClient
    from .config_schema import DEFAULT_BASE_URL

    base = os.environ.get("OPENLOFT_MEMORY_BASE_URL")
    cfg_file = _hermes_home() / "openloft-memory.json"
    if base is None and cfg_file.exists():
        try:
            base = json.loads(cfg_file.read_text()).get("base_url")
        except Exception:  # noqa: BLE001 - a corrupt sidecar must not kill the CLI
            base = None
    return ServiceClient(base_url=base or DEFAULT_BASE_URL, api_key=key)


def _run(args) -> None:
    client = _backend()
    if args.subcommand == "health":
        try:
            stats = client.stats(agent_id=_agent_id())
            print(f"provisioned · backend reachable · turn {stats.get('turn')}")
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            if "provision" in detail.lower():
                print("not provisioned — mint a key from the dashboard, then re-run.")
                sys.exit(3)
            print(f"unreachable: {detail}")
            sys.exit(2)
    elif args.subcommand == "stats":
        print(json.dumps(client.stats(agent_id=_agent_id()), indent=2))
    elif args.subcommand == "search":
        print(json.dumps(
            client.search(args.query, agent_id=_agent_id(), limit=args.limit), indent=2))
    elif args.subcommand == "forget":
        print("Forget = TEMPORAL INVALIDATION. The fact stops being retrieved;")
        print("the write-ahead log retains it. This is not erasure.")
        if not args.yes and input("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            sys.exit(1)
        print(json.dumps(client.forget(
            agent_id=_agent_id(),
            entity_name=None if args.record_id else args.target,
            record_id=args.record_id,
        ), indent=2))


def register_cli(subparser) -> None:
    """Called by the memory plugin CLI discovery at argparse setup time."""
    subparser.description = "Interact with OpenLoft memory"
    subs = subparser.add_subparsers(dest="subcommand", required=False)

    health = subs.add_parser("health", help="Verify the tenant is provisioned and reachable")
    health.set_defaults(func=_run, subcommand="health")

    stats = subs.add_parser("stats", help="Turn number and store counts")
    stats.set_defaults(func=_run, subcommand="stats")

    search = subs.add_parser("search", help="Scored search over stored records")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=_run, subcommand="search")

    forget = subs.add_parser("forget", help="Stop retrieving a stored fact (not erasure)")
    forget.add_argument("target", help="Entity name to forget")
    forget.add_argument("--record-id", default=None,
                        help="Forget one record instead of a whole entity")
    forget.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    forget.set_defaults(func=_run, subcommand="forget")

    # Bare `hermes openloft-memory` falls back to stats.
    subparser.set_defaults(func=_run, subcommand="stats")


__all__ = ["register_cli"]
