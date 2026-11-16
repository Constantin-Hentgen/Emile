"""Users, API keys, households and memberships: shared by the CLI and the admin endpoints."""

import datetime
import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from emile.schemas import HouseholdOut, Member
from emile.service.tables import ApiKey, Household, Membership, ShopLink, User

KEY_PREFIX = "emile_"
SHOP_PREFIX = "shop_"


class AccountError(ValueError):
    """Invalid account operation (duplicate name, unknown user...); the message is safe to show."""


def hash_key(key: str) -> str:
    """Keys are 256-bit random secrets, so a fast hash is enough (no password stretching needed)."""
    return hashlib.sha256(key.encode()).hexdigest()


def create_user(session: Session, name: str, now: datetime.datetime) -> User:
    name = " ".join(name.split())
    if not name:
        raise AccountError("a user needs a name")
    if session.scalar(select(User).where(User.name == name)) is not None:
        raise AccountError(f"user {name!r} already exists")
    user = User(name=name, created_at=now)
    session.add(user)
    session.flush()
    return user


def find_user(session: Session, ref: str | int) -> User:
    """A user by id or by name."""
    user = (
        session.get(User, int(ref))
        if isinstance(ref, int) or str(ref).isdigit()
        else session.scalar(select(User).where(User.name == ref))
    )
    if user is None:
        raise AccountError(f"no user {ref!r}")
    return user


def create_key(session: Session, user: User, label: str | None, now: datetime.datetime) -> tuple[ApiKey, str]:
    """A new key for ``user``: returns the stored row and the secret, which is never stored and never shown again."""
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(user_id=user.id, label=label, prefix=secret[:12], key_hash=hash_key(secret), created_at=now)
    session.add(key)
    session.flush()
    return key, secret


def revoke_key(session: Session, key_id: int, now: datetime.datetime) -> ApiKey:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise AccountError(f"no key {key_id}")
    key.revoked_at = key.revoked_at or now
    return key


@dataclass(frozen=True, slots=True)
class Caller:
    user_id: int
    name: str
    key_id: int


def authenticate(session: Session, secret: str, now: datetime.datetime) -> Caller | None:
    """The caller holding ``secret``, if the key exists and is not revoked; records when it was last used."""
    key = session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(secret)))
    if key is None or key.revoked_at is not None:
        return None
    user = session.get_one(User, key.user_id)
    # One write per minute at most: last_used is for humans auditing keys, not worth a write per request.
    if key.last_used_at is None or now - key.last_used_at > datetime.timedelta(minutes=1):
        session.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
    return Caller(user.id, user.name, key.id)


def create_shop_link(
    session: Session, household_id: int, label: str | None, now: datetime.datetime
) -> tuple[ShopLink, str]:
    """A new shop link for a household: returns the row and the secret, which is never stored."""
    secret = SHOP_PREFIX + secrets.token_urlsafe(32)
    link = ShopLink(
        household_id=household_id,
        label=" ".join(label.split())[:80] or None if label else None,
        prefix=secret[:11],
        token_hash=hash_key(secret),
        created_at=now,
    )
    session.add(link)
    session.flush()
    return link, secret


def revoke_shop_link(session: Session, household_id: int, link_id: int, now: datetime.datetime) -> ShopLink:
    link = session.get(ShopLink, link_id)
    if link is None or link.household_id != household_id:
        raise AccountError(f"no shop link {link_id}")
    link.revoked_at = link.revoked_at or now
    return link


def authenticate_shop(session: Session, secret: str, now: datetime.datetime) -> int | None:
    """The household id a shop link opens, if the link exists and is not revoked."""
    link = session.scalar(select(ShopLink).where(ShopLink.token_hash == hash_key(secret)))
    if link is None or link.revoked_at is not None:
        return None
    if link.last_used_at is None or now - link.last_used_at > datetime.timedelta(minutes=1):
        session.execute(update(ShopLink).where(ShopLink.id == link.id).values(last_used_at=now))
    return link.household_id


def create_household(
    session: Session,
    name: str,
    *,
    timezone: str,
    language: str,
    currency: str,
    meal_boxes: int,
    members: list[User],
    now: datetime.datetime,
) -> Household:
    name = " ".join(name.split())
    if not name:
        raise AccountError("a household needs a name")
    household = Household(
        name=name,
        timezone=timezone,
        language=language,
        currency=currency,
        meal_boxes=meal_boxes,
        next_list_id=1,
        next_stock_id=1,
        version=0,
        created_at=now,
    )
    session.add(household)
    session.flush()
    for user in members:
        add_member(session, household, user, now)
    return household


def find_household(session: Session, household_id: int) -> Household:
    household = session.get(Household, household_id)
    if household is None:
        raise AccountError(f"no household {household_id}")
    return household


def add_member(session: Session, household: Household, user: User, now: datetime.datetime) -> None:
    if session.get(Membership, (household.id, user.id)) is not None:
        return
    session.add(Membership(household_id=household.id, user_id=user.id, created_at=now))
    try:
        session.flush()
    except IntegrityError:
        raise AccountError("could not add the member") from None


def households_of(session: Session, user_id: int) -> list[Household]:
    return list(
        session.scalars(
            select(Household)
            .join(Membership, Membership.household_id == Household.id)
            .where(Membership.user_id == user_id)
            .order_by(Household.id)
        )
    )


def is_member(session: Session, household_id: int, user_id: int) -> bool:
    return session.get(Membership, (household_id, user_id)) is not None


def household_out(session: Session, household: Household) -> HouseholdOut:
    members = session.execute(
        select(User.id, User.name)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.household_id == household.id)
        .order_by(User.id)
    )
    return HouseholdOut(
        id=household.id,
        name=household.name,
        timezone=household.timezone,
        language=household.language,
        currency=household.currency,
        meal_boxes=household.meal_boxes,
        members=[Member(id=i, name=n) for i, n in members],
    )
