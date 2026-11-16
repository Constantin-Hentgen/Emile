"""The shop page: a revocable link that can read the current list and tick items, and nothing else.

Why a separate credential: the page runs on a phone in a shop and its secret sits in the browser's storage and
history. An API key there would hand over the stock, the calendar password's owner and every household write; a shop
link can only tick what is already on the list.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from emile.core import Kitchen, ShoppingList, render, shopping
from emile.schemas import ShopGroup, ShopItem, ShopLinkCreate, ShopLinkCreated, ShopLinkOut, ShopView
from emile.service import accounts, repository
from emile.service.deps import DbDep, HouseholdDep, LimitsDep, NowDep, SettingsDep, bearer
from emile.service.repository import HouseholdInfo
from emile.service.shop_page import PAGE
from emile.service.tables import ShopLink


def shop_enabled(settings: SettingsDep) -> None:
    if not settings.shop_page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the shop page is off (EMILE_SHOP_PAGE=false)")


def shop_household(
    db: DbDep,
    now: NowDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> int:
    """The household a shop link opens. API keys are refused here on purpose: the two credentials never mix."""
    secret = credentials.credentials if credentials else ""
    if not secret.startswith(accounts.SHOP_PREFIX):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "a shop link is required", headers={"WWW-Authenticate": "Bearer"}
        )
    with db.session() as session:
        household_id = accounts.authenticate_shop(session, secret, now)
    if household_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or revoked shop link", headers={"WWW-Authenticate": "Bearer"}
        )
    return household_id


ShopHouseholdDep = Annotated[int, Depends(shop_household)]

router = APIRouter(prefix="/v1/shop", tags=["shop page"], dependencies=[Depends(shop_enabled)])
links_router = APIRouter(prefix="/v1/shop-links", tags=["shop page"])
page_router = APIRouter(tags=["shop page"], dependencies=[Depends(shop_enabled)])


def _view(lst: ShoppingList, info: HouseholdInfo) -> ShopView:
    lang: render.Lang = "en" if info.language == "en" else "fr"
    groups = [
        ShopGroup(
            aisle=aisle, items=[ShopItem(index=i, label=render.item_label(item), done=item.done) for i, item in entries]
        )
        for aisle, entries in render.group_by_aisle(lst.items, lang).items()
    ]
    return ShopView(
        list_id=lst.id,
        language=lang,
        summary=render.summary_line(lst, lang, info.currency),
        groups=groups,
        total=len(lst.items),
        done=sum(item.done for item in lst.items),
    )


@router.get("")
def get_shop(db: DbDep, household_id: ShopHouseholdDep) -> ShopView:
    """The current list for the page (404 `no_current_list` when the household has none)."""
    return repository.read(db, household_id, lambda kitchen, info: _view(shopping.current(kitchen), info))


@router.post("/lists/{list_id}/items/{index}/toggle")
def toggle(
    list_id: int, index: int, db: DbDep, household_id: ShopHouseholdDep, now: NowDep, limits: LimitsDep
) -> ShopView:
    """Same rule as `POST /v1/lists/{id}/items/{index}/toggle`: ticked = bought. 409 `stale_list` if replaced."""

    def run(kitchen: Kitchen, info: HouseholdInfo) -> ShopView:
        lst = shopping.toggle(kitchen, list_id, index, today=info.today(now), limits=limits)
        return _view(lst, info)

    return repository.mutate(db, household_id, run)


@router.post("/lists/{list_id}/reset")
def reset(list_id: int, db: DbDep, household_id: ShopHouseholdDep) -> ShopView:
    """Untick everything and take those items back out of the stock."""
    return repository.mutate(db, household_id, lambda kitchen, info: _view(shopping.reset(kitchen, list_id), info))


@page_router.get("/shop", response_class=HTMLResponse, include_in_schema=False)
def page() -> HTMLResponse:
    """The page itself: static, with no secret and no data. The link's secret is in the URL fragment."""
    return HTMLResponse(
        PAGE,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        },
    )


# --- managing links, with an ordinary API key ----------------------------------------------------------------------


def _out(link: ShopLink) -> ShopLinkOut:
    return ShopLinkOut(
        id=link.id,
        label=link.label,
        prefix=link.prefix,
        created_at=link.created_at,
        last_used_at=link.last_used_at,
        revoked=link.revoked_at is not None,
    )


@links_router.post("", status_code=201)
def create_link(body: ShopLinkCreate, db: DbDep, household: HouseholdDep, now: NowDep) -> ShopLinkCreated:
    """Make a link for the shop page. The secret is shown once: open `<server>/shop#<token>` on the phone."""
    with db.session() as session:
        link, secret = accounts.create_shop_link(session, household.id, body.label, now)
        return ShopLinkCreated(**_out(link).model_dump(), token=secret, path=f"/shop#{secret}")


@links_router.get("")
def list_links(db: DbDep, household: HouseholdDep) -> list[ShopLinkOut]:
    with db.session() as session:
        rows = session.scalars(select(ShopLink).where(ShopLink.household_id == household.id).order_by(ShopLink.id))
        return [_out(link) for link in rows]


@links_router.delete("/{link_id}", status_code=204)
def revoke_link(link_id: int, db: DbDep, household: HouseholdDep, now: NowDep) -> Response:
    """Revoke a link: the page stops working on the phones that had it."""
    with db.session() as session:
        accounts.revoke_shop_link(session, household.id, link_id, now)
    return Response(status_code=204)
