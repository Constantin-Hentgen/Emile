"""Take the household's data out."""

from fastapi import APIRouter

from emile.core import Kitchen
from emile.schemas import Export, ExportHousehold
from emile.service import repository
from emile.service.deps import DbDep, HouseholdDep, NowDep
from emile.service.repository import HouseholdInfo

router = APIRouter(prefix="/v1", tags=["account"])


@router.get("/export")
def export(db: DbDep, household: HouseholdDep, now: NowDep) -> Export:
    """Everything the household has in Emile: settings, current list and stock. No secrets are included."""

    def dump(kitchen: Kitchen, info: HouseholdInfo) -> Export:
        settings = ExportHousehold(
            name=info.name,
            timezone=info.timezone.key,
            language="en" if info.language == "en" else "fr",
            currency=info.currency,
            meal_boxes=info.meal_boxes,
        )
        return Export(exported_at=now, household=settings, kitchen=kitchen)

    return repository.read(db, household.id, dump)
