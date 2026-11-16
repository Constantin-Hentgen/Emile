"""One task per item: stable UIDs, the VTODO text, and the two-way merge rule."""

import datetime

from emile.core import NewItem, ical, shopping, tasks
from emile.core.tasks import RemoteTask
from emile.service.remote import parse_task
from tests.core.conftest import NOW


def make(kitchen):
    lst = shopping.new_list(
        kitchen,
        [NewItem(name="Bananes", qty="7", aisle="🥬 Fruits"), NewItem(name="Lait"), NewItem(name="Pain", aisle="🥖")],
        now=NOW,
    )
    shopping.attach_event(kitchen, lst.id, "token@emile-shopping")
    return kitchen.current_list


def remote_of(lst, token="token@emile-shopping", **done):
    """The tasks a calendar would hold for ``lst``; ``done`` overrides by item name."""
    uids = tasks.item_uids(lst, token)
    return {
        uid: RemoteTask(uid, *tasks.wanted(item), done=done.get(item.name, item.done))
        for uid, item in zip(uids, lst.items, strict=True)
    }


def test_uids_are_stable_across_edits_and_unique():
    from emile.core import Kitchen

    kitchen = Kitchen()
    lst = make(kitchen)
    before = dict(zip((i.name for i in lst.items), tasks.item_uids(lst, "token@emile-shopping"), strict=True))
    assert len(set(before.values())) == 3
    assert all(uid.endswith(ical.UID_SUFFIX) for uid in before.values())
    edited, __ = shopping.edit(kitchen, remove=["lait"], add=[NewItem(name="Riz")], today=NOW.date())
    after = dict(zip((i.name for i in edited.items), tasks.item_uids(edited, edited.event_uid or ""), strict=True))
    assert (after["Bananes"], after["Pain"]) == (before["Bananes"], before["Pain"])  # same tasks, new positions
    assert tasks.item_uids(lst, "other@emile-shopping") != tasks.item_uids(lst, "token@emile-shopping")


def test_the_same_name_twice_still_gets_two_uids(kitchen):
    lst = make(kitchen)
    lst.items.append(lst.items[0].model_copy())
    assert len(set(tasks.item_uids(lst, "t"))) == 4


def test_todo_text():
    now = datetime.datetime(2026, 11, 10, 8, 0, tzinfo=datetime.UTC)
    open_task = tasks.build_todo("u@emile-shopping", "Bananes — 7, mûres", "🥬 Fruits", False, now=now)
    assert open_task == (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//emile//groceries//EN\r\nBEGIN:VTODO\r\nUID:u@emile-shopping\r\n"
        "DTSTAMP:20261110T080000Z\r\nSUMMARY:Bananes — 7\\, mûres\r\nCATEGORIES:🥬 Fruits\r\n"
        "STATUS:NEEDS-ACTION\r\nSEQUENCE:0\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )
    done = tasks.build_todo("u@emile-shopping", "Lait", None, True, now=now, sequence=2)
    assert "STATUS:COMPLETED\r\nCOMPLETED:20261110T080000Z\r\nPERCENT-COMPLETE:100\r\nSEQUENCE:2" in done
    assert "CATEGORIES" not in done
    parsed = parse_task(open_task)
    assert (parsed.summary, parsed.category, parsed.done) == ("Bananes — 7, mûres", "🥬 Fruits", False)
    assert parse_task(done).done is True


def test_phone_apps_that_only_set_the_completed_date_count_as_done():
    bare = (
        "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:x@emile-shopping\r\nSUMMARY:Lait\r\n"
        "COMPLETED:20261110T080000Z\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )
    assert parse_task(bare).done is True
    assert parse_task(bare.replace("COMPLETED:", "STATUS:NEEDS-ACTION\r\nCOMPLETED:")).done is False


# --- the merge rule ---------------------------------------------------------------------------------------------


def uid_of(lst, name):
    return next(
        u for u, i in zip(tasks.item_uids(lst, "token@emile-shopping"), lst.items, strict=True) if i.name == name
    )


def test_nothing_to_do_when_both_sides_agree(kitchen):
    lst = make(kitchen)
    plan = tasks.plan(lst, "token@emile-shopping", remote_of(lst), {})
    assert (plan.pull, plan.push, plan.delete) == ({}, [], [])
    assert set(plan.state.values()) == {False}


def test_missing_tasks_are_created_even_after_a_deletion_in_the_calendar(kitchen):
    lst = make(kitchen)
    remote = remote_of(lst)
    gone = uid_of(lst, "Lait")
    del remote[gone]
    plan = tasks.plan(lst, "token@emile-shopping", remote, {gone: False})
    assert plan.push == [gone]


def test_completed_in_the_calendar_ticks_the_item(kitchen):
    lst = make(kitchen)
    last = dict.fromkeys(tasks.item_uids(lst, "token@emile-shopping"), False)
    plan = tasks.plan(lst, "token@emile-shopping", remote_of(lst, Lait=True), last)
    assert plan.pull == {uid_of(lst, "Lait"): True}
    assert plan.push == [] and plan.state[uid_of(lst, "Lait")] is True


def test_unticked_in_the_calendar_unticks_the_item(kitchen):
    lst = make(kitchen)
    lst.items[1].done = True
    last = {u: i.done for u, i in zip(tasks.item_uids(lst, "token@emile-shopping"), lst.items, strict=True)}
    plan = tasks.plan(lst, "token@emile-shopping", remote_of(lst, Lait=False), last)
    assert plan.pull == {uid_of(lst, "Lait"): False}


def test_ticked_through_the_api_completes_the_task(kitchen):
    lst = make(kitchen)
    last = dict.fromkeys(tasks.item_uids(lst, "token@emile-shopping"), False)
    remote = remote_of(lst)
    lst.items[0].done = True  # ticked through the API since the last sync
    plan = tasks.plan(lst, "token@emile-shopping", remote, last)
    assert plan.push == [uid_of(lst, "Bananes")] and plan.pull == {}


def test_without_a_record_the_list_wins_a_disagreement(kitchen):
    lst = make(kitchen)
    remote = remote_of(lst, Lait=True)  # the calendar says bought, the list says not, and nothing is recorded
    plan = tasks.plan(lst, "token@emile-shopping", remote, {})
    assert plan.pull == {} and plan.push == [uid_of(lst, "Lait")]
    assert plan.state[uid_of(lst, "Lait")] is False


def test_renamed_or_recategorised_tasks_are_rewritten(kitchen):
    lst = make(kitchen)
    remote = remote_of(lst)
    remote[uid_of(lst, "Bananes")] = RemoteTask(uid_of(lst, "Bananes"), "Bananes — 5", "🥬 Fruits", False)
    assert tasks.plan(lst, "token@emile-shopping", remote, {}).push == [uid_of(lst, "Bananes")]


def test_tasks_of_removed_items_and_old_lists_are_deleted(kitchen):
    lst = make(kitchen)
    remote = remote_of(lst)
    remote["old@emile-shopping"] = RemoteTask("old@emile-shopping", "Riz", None, True)
    assert tasks.plan(lst, "token@emile-shopping", remote, {}).delete == ["old@emile-shopping"]
