from app.persistence import SQLiteStore


def test_store_bounds_policy_history_and_saves_experiment(tmp_path):
    store = SQLiteStore(tmp_path / "pulse.db", snapshot_limit=2)
    store.initialize()
    for version in range(1, 4):
        store.save_policy(version, {"version": version})

    assert [item["version"] for item in store.recent_policies()] == [3, 2]
    assert store.save_experiment("burst", {"requests": 10}) > 0


def test_policy_versions_do_not_overwrite_previous_process(tmp_path):
    path = tmp_path / "pulse.db"
    first = SQLiteStore(path, snapshot_limit=5)
    first.initialize()
    first.save_policy(1, {"run": "first"})
    second = SQLiteStore(path, snapshot_limit=5)
    second.initialize()
    second.save_policy(1, {"run": "second"})

    assert second.recent_policies() == [{"run": "second"}, {"run": "first"}]
