"""``emile`` command line: run the server, migrate the database, manage users, households, API keys and shop links.

In Docker: ``docker exec emile emile setup bob --household Home`` creates a user, a household and an API key.
"""

import argparse
import datetime
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the service stack is imported lazily: `emile --help` must work without the service extra
    from sqlalchemy.orm import Session

    from emile.service.settings import Settings


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point of the ``emile`` script."""
    parser = argparse.ArgumentParser(prog="emile", description="Emile groceries service")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", help="bind address (default: EMILE_HOST)")
    serve.add_argument("--port", type=int, help="port (default: EMILE_PORT)")

    db = sub.add_parser("db", help="database").add_subparsers(dest="action", required=True)
    db.add_parser("upgrade", help="apply pending migrations")

    users = sub.add_parser("users", help="users").add_subparsers(dest="action", required=True)
    users.add_parser("create", help="create a user").add_argument("name")
    users.add_parser("list", help="list users")

    keys = sub.add_parser("keys", help="API keys").add_subparsers(dest="action", required=True)
    create_key = keys.add_parser("create", help="create an API key (printed once)")
    create_key.add_argument("user", help="user name or id")
    create_key.add_argument("--label", help="what the key is for, e.g. 'bob'")
    keys.add_parser("revoke", help="revoke a key").add_argument("key_id", type=int)
    keys.add_parser("list", help="list keys (never their secrets)")

    shop = sub.add_parser("shop-link", help="links that open the mobile shop page").add_subparsers(
        dest="action", required=True
    )
    shop_create = shop.add_parser("create", help="create a shop link (printed once)")
    shop_create.add_argument("household_id", type=int)
    shop_create.add_argument("--label", help="what the link is for, e.g. 'Anna's phone'")
    shop.add_parser("list", help="list shop links (never their secrets)")
    shop.add_parser("revoke", help="revoke a shop link").add_argument("link_id", type=int)

    households = sub.add_parser("households", help="households").add_subparsers(dest="action", required=True)
    create_household = households.add_parser("create", help="create a household")
    create_household.add_argument("name")
    _household_options(create_household)
    create_household.add_argument("--member", action="append", default=[], help="user name or id (repeatable)")
    add_member = households.add_parser("add-member", help="add a user to a household")
    add_member.add_argument("household_id", type=int)
    add_member.add_argument("user", help="user name or id")
    households.add_parser("list", help="list households")

    setup = sub.add_parser("setup", help="create a user, a household and an API key in one go")
    setup.add_argument("user", help="user name (reused if it exists)")
    setup.add_argument("--household", help="household name (default: the user's name)")
    setup.add_argument("--label", help="key label (default: the user's name)")
    _household_options(setup)

    imports = sub.add_parser("import", help="one-shot imports").add_subparsers(dest="action", required=True)
    import_bob = imports.add_parser("bob", help="import the shopping list and stock of a Bob state.json")
    import_bob.add_argument("file", help="path to state.json, or - for standard input")
    import_bob.add_argument("--household", type=int, required=True, help="household id (see `emile households list`)")
    import_bob.add_argument("--replace", action="store_true", help="overwrite a kitchen that holds different data")

    args = parser.parse_args(argv)
    if args.command == "serve":
        _serve(args.host, args.port)
        return
    handlers: dict[tuple[str, str], Callable[[Session, argparse.Namespace, Settings, datetime.datetime], None]] = {
        ("users", "create"): _users_create,
        ("users", "list"): _users_list,
        ("keys", "create"): _keys_create,
        ("keys", "revoke"): _keys_revoke,
        ("keys", "list"): _keys_list,
        ("shop-link", "create"): _shop_create,
        ("shop-link", "list"): _shop_list,
        ("shop-link", "revoke"): _shop_revoke,
        ("households", "create"): _households_create,
        ("households", "add-member"): _households_add_member,
        ("households", "list"): _households_list,
        ("setup", ""): _setup,
    }
    if args.command == "import":
        _import_bob(args)
        return
    if args.command == "db":
        _with_db(None)
        print("database up to date")
        return
    _with_db(handlers[(args.command, getattr(args, "action", ""))], args)


def _household_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timezone", help="IANA time zone (default: EMILE_DEFAULT_TIMEZONE)")
    parser.add_argument("--language", choices=["fr", "en"], help="default: EMILE_DEFAULT_LANGUAGE")
    parser.add_argument("--currency", help="default: EMILE_DEFAULT_CURRENCY")
    parser.add_argument("--boxes", type=int, default=0, help="meal-prep boxes owned (default: 0)")


def _serve(host: str | None, port: int | None) -> None:
    import uvicorn

    from emile.service.app import create_app
    from emile.service.logging import configure_logging
    from emile.service.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(create_app(settings), host=host or settings.host, port=port or settings.port, log_config=None)


def _with_db(handler: Callable[..., None] | None, args: argparse.Namespace | None = None) -> None:
    """Migrate, then run ``handler`` in one transaction; account errors exit with status 1 and a clean message."""
    from emile.service import migrate
    from emile.service.accounts import AccountError
    from emile.service.db import Database
    from emile.service.settings import get_settings

    settings = get_settings()
    database = Database(settings.database_url)
    try:
        migrate.upgrade(database)
        if handler is not None:
            with database.session() as session:
                handler(session, args, settings, datetime.datetime.now(datetime.UTC))
    except AccountError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        database.dispose()


def _users_create(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    user = accounts.create_user(session, args.name, now)
    print(f"user {user.id}: {user.name}")


def _users_list(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from sqlalchemy import select

    from emile.service.tables import User

    for user in session.scalars(select(User).order_by(User.id)):
        print(f"{user.id}\t{user.name}")


def _print_key(key: Any, secret: str) -> None:
    print(f"key {key.id} for user {key.user_id} ({key.label or 'no label'}). Copy it now, it is not stored:")
    print(secret)


def _keys_create(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    key, secret = accounts.create_key(session, accounts.find_user(session, args.user), args.label, now)
    _print_key(key, secret)


def _keys_revoke(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    key = accounts.revoke_key(session, args.key_id, now)
    print(f"key {key.id} ({key.prefix}…) revoked")


def _keys_list(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from sqlalchemy import select

    from emile.service.tables import ApiKey, User

    rows = session.execute(select(ApiKey, User.name).join(User, User.id == ApiKey.user_id).order_by(ApiKey.id))
    for key, user in rows:
        state = (
            "revoked" if key.revoked_at else f"last used {key.last_used_at:%Y-%m-%d}" if key.last_used_at else "unused"
        )
        print(f"{key.id}\t{key.prefix}…\t{user}\t{key.label or ''}\t{state}")


def _shop_create(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    household = accounts.find_household(session, args.household_id)
    link, secret = accounts.create_shop_link(session, household.id, args.label, now)
    print(f"shop link {link.id} for household {household.id}. Open this on the phone (it is not stored):")
    print(f"  <server address>/shop#{secret}")


def _shop_list(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from sqlalchemy import select

    from emile.service.tables import ShopLink

    for link in session.scalars(select(ShopLink).order_by(ShopLink.id)):
        state = (
            "revoked"
            if link.revoked_at
            else f"last used {link.last_used_at:%Y-%m-%d}"
            if link.last_used_at
            else "unused"
        )
        print(f"{link.id}\t{link.prefix}…\thousehold {link.household_id}\t{link.label or ''}\t{state}")


def _shop_revoke(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service.tables import ShopLink

    link = session.get(ShopLink, args.link_id)
    if link is None:
        raise SystemExit(f"no shop link {args.link_id}")
    link.revoked_at = link.revoked_at or now
    print(f"shop link {link.id} ({link.prefix}…) revoked")


def _new_household(
    session: "Session", name: str, args: Any, settings: "Settings", members: list[Any], now: datetime.datetime
) -> Any:
    from emile.schemas import _check_timezone
    from emile.service import accounts

    try:
        timezone = _check_timezone(args.timezone) or settings.default_timezone
    except ValueError as e:
        raise accounts.AccountError(str(e)) from None
    return accounts.create_household(
        session,
        name,
        timezone=timezone,
        language=args.language or settings.default_language,
        currency=args.currency or settings.default_currency,
        meal_boxes=max(0, args.boxes),
        members=members,
        now=now,
    )


def _households_create(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    members = [accounts.find_user(session, ref) for ref in args.member]
    household = _new_household(session, args.name, args, settings, members, now)
    print(f"household {household.id}: {household.name} ({household.timezone}, {household.language})")


def _households_add_member(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    household = accounts.find_household(session, args.household_id)
    user = accounts.find_user(session, args.user)
    accounts.add_member(session, household, user, now)
    print(f"{user.name} is a member of household {household.id}")


def _households_list(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from sqlalchemy import select

    from emile.service import accounts
    from emile.service.tables import Household

    for household in session.scalars(select(Household).order_by(Household.id)):
        out = accounts.household_out(session, household)
        members = ", ".join(m.name for m in out.members)
        print(f"{out.id}\t{out.name}\t{out.timezone}\t{out.language}\t{members}")


def _setup(session: "Session", args: Any, settings: "Settings", now: datetime.datetime) -> None:
    from emile.service import accounts

    try:
        user = accounts.find_user(session, args.user)
    except accounts.AccountError:
        user = accounts.create_user(session, args.user, now)
    print(f"user {user.id}: {user.name}")
    household = _new_household(session, args.household or user.name, args, settings, [user], now)
    print(f"household {household.id}: {household.name} ({household.timezone}, {household.language})")
    key, secret = accounts.create_key(session, user, args.label or user.name, now)
    _print_key(key, secret)


def _import_bob(args: argparse.Namespace) -> None:
    import json
    from pathlib import Path

    from emile.core import EmileError
    from emile.service import migrate
    from emile.service.db import Database
    from emile.service.repository import WouldOverwrite
    from emile.service.routes.imports import import_bob_state
    from emile.service.settings import get_settings
    from emile.service.tables import Household

    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError("expected a JSON object")
    except (OSError, ValueError) as e:
        print(f"error: cannot read {args.file}: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        migrate.upgrade(database)
        with database.session() as session:
            if session.get(Household, args.household) is None:
                print(f"error: no household {args.household}", file=sys.stderr)
                raise SystemExit(1)
        result = import_bob_state(
            database,
            args.household,
            state,
            now=datetime.datetime.now(datetime.UTC),
            limits=settings.limits,
            replace=args.replace,
        )
    except (EmileError, WouldOverwrite) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        database.dispose()
    status = "imported" if result.changed else "already imported, nothing changed"
    list_part = f"list {result.list_id} ({result.list_items} items)" if result.list_id else "no list"
    print(f"{status}: {list_part}, {result.stock_entries} stock entries")
