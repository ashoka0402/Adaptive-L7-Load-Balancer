from app.persistence.sqlite import SQLiteStore


def test_store_bounds_experiment_history(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.initialize()
    for i in range(10):
        store.save_experiment(str(i), {"requests": i})
    assert store.get_experiment("9") == {"requests": 9}
    assert store.get_experiment("0") is None
    assert len(store.recent_experiments()) == 8
