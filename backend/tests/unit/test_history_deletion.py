from datetime import timedelta

import pytest

from app.core.errors import ApiError


def seed(env, number, user="1001", status="completed"):
    task_id = f"tsk_{number:020x}"
    env.store.set(f"tasks/{task_id}", {
        "id": task_id,
        "userId": user,
        "status": status,
        "createdAt": env.clock() - timedelta(minutes=number),
    })
    return task_id


def test_delete_history_is_owner_scoped_and_preserves_active_tasks(env):
    headers = env.headers()
    finished = seed(env, 1)
    active = seed(env, 2, status="running")
    gate = seed(env, 3, status="awaiting_approval")
    foreign = seed(env, 4, user="1002")
    service = env.app.state.task_service
    response = env.client.delete("/api/v1/tasks/history", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    rows, _ = service.list_tasks("1001", 20, None)
    assert {row["id"] for row in rows} == {active, gate}
    assert not env.store.get(f"tasks/{foreign}").get("historyDeletedAt")
    with pytest.raises(ApiError) as error:
        service.get_task("1001", finished)
    assert error.value.status_code == 404
    assert env.client.delete("/api/v1/tasks/history", headers=headers).json() == {"deleted": 0}


def test_deleted_rows_do_not_break_pagination_or_quota_counts(env):
    env.headers()
    for number in range(1, 62):
        seed(env, number)
    service = env.app.state.task_service
    before = env.store.count_user_tasks_since("1001", env.clock() - timedelta(days=1))
    assert service.delete_history("1001") == 61
    visible = [seed(env, 80, status="running"), seed(env, 81, status="awaiting_start")]
    rows, cursor = service.list_tasks("1001", 1, None)
    assert [row["id"] for row in rows] == visible[:1]
    assert cursor == visible[0]
    rows, cursor = service.list_tasks("1001", 1, cursor)
    assert [row["id"] for row in rows] == visible[1:]
    assert cursor is None
    assert env.store.count_user_tasks_since("1001", env.clock() - timedelta(days=1)) == before + 2


def test_delete_history_requires_authentication(env):
    assert env.client.delete("/api/v1/tasks/history").status_code == 401
