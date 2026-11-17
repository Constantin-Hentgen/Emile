"""The aisle order learned for a shop."""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import delete

from emile.core import aisles
from emile.schemas import AisleOrder
from emile.service.deps import DbDep, HouseholdDep
from emile.service.repository import ranks_of
from emile.service.tables import AisleRank

router = APIRouter(prefix="/v1/aisle-order", tags=["shopping list"])

ShopQuery = Annotated[str, Query(min_length=1, max_length=60, description="The shop's name, as given to the list.")]


@router.get("")
def get_order(shop: ShopQuery, db: DbDep, household: HouseholdDep) -> AisleOrder:
    """The aisles of this shop in the order they are usually started (only those seen on at least 2 trips)."""
    with db.session() as session:
        order = aisles.order(ranks_of(session, household.id, shop))
    return AisleOrder(shop=shop, aisles=sorted(order, key=lambda aisle: order[aisle]))


@router.delete("", status_code=204)
def forget_order(shop: ShopQuery, db: DbDep, household: HouseholdDep) -> Response:
    """Forget what was learned for this shop."""
    with db.session() as session:
        session.execute(
            delete(AisleRank).where(AisleRank.household_id == household.id, AisleRank.shop == aisles.key(shop))
        )
    return Response(status_code=204)
