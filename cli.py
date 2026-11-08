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


def _config_path() -> Path:
    """Same file the Desktop panel edits (STORAGE_FLAT_JSON convention)."""
    from .config_schema import runtime_config_path

    return runtime_config_path(str(_hermes_home()))


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
    cfg_file = _config_path()
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
    elif args.subcommand == "remember":
        print("Remembering via OpenLoft memory…")
        print(json.dumps(client.remember(
            [{"role": "user", "content": args.text}],
            agent_id=_agent_id(),
        ), indent=2))
    elif args.subcommand == "recall":
        print("Recalling from OpenLoft memory…")
        print(json.dumps(client.recall(
            args.query,
            agent_id=_agent_id(),
            top_k=args.top_k,
        ), indent=2))
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
    elif args.subcommand == "backfill":
        from .provider import OpenLoftMemoryProvider

        if not args.sessions_file:
            print("backfill needs a sessions file: --sessions-file <path>")
            print("The session list comes from Hermes' session-history surface")
            print("(stub adapter — see cli.py backfill TODO).")
            sys.exit(2)
        try:
            sessions = json.loads(Path(args.sessions_file).read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"could not read sessions file: {exc}")
            sys.exit(2)

        prov = OpenLoftMemoryProvider()
        prov.initialize("cli", hermes_home=str(_hermes_home()), platform="cli",
                        agent_identity=os.environ.get("HERMES_PROFILE", ""))
        result = prov.backfill(
            sessions,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))


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

    remember = subs.add_parser("remember",
                               help="Store a fact in OpenLoft memory")
    remember.add_argument("text", help="Text to remember")
    remember.set_defaults(func=_run, subcommand="remember")

    recall = subs.add_parser("recall",
                             help="Recall relevant facts from OpenLoft memory")
    recall.add_argument("query", help="What to recall")
    recall.add_argument("--top-k", type=int, default=8,
                        help="Max number of facts to return")
    recall.set_defaults(func=_run, subcommand="recall")

    forget = subs.add_parser("forget", help="Stop retrieving a stored fact (not erasure)")
    forget.add_argument("target", help="Entity name to forget")
    forget.add_argument("--record-id", default=None,
                        help="Forget one record instead of a whole entity")
    forget.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    forget.set_defaults(func=_run, subcommand="forget")

    backfill = subs.add_parser("backfill",
                               help="Ingest historical session transcripts into memory")
    backfill.add_argument("--sessions-file", default=None,
                          help="JSON file with session list (Hermes-dependent source)")
    backfill.add_argument("--limit", type=int, default=None,
                          help="Max sessions to ingest in this run")
    backfill.add_argument("--dry-run", action="store_true",
                          help="Show what would be ingested without writing")
    backfill.set_defaults(func=_run, subcommand="backfill")

    # Bare `hermes openloft-memory` falls back to stats.
    subparser.set_defaults(func=_run, subcommand="stats")


__all__ = ["register_cli"]
