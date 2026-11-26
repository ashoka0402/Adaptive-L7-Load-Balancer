from app.fastapi_lb.routing import Backend, Pool, RouteTrie


def test_longest_segment_prefix():
    default, users, nested = Pool([]), Pool([]), Pool([])
    trie = RouteTrie()
    trie.insert("/", default)
    trie.insert("/users", users)
    trie.insert("/users/profile", nested)
    assert trie.match("/users/profile/42") is nested
    assert trie.match("/users/42") is users
    assert trie.match("/users-old") is default


def test_latency_aware_samples_and_penalizes_load():
    a = Backend("a", "http://a", ewma=10, active=10)
    b = Backend("b", "http://b", ewma=30)
    c = Backend("c", "http://c")
    pool = Pool([a, b, c], "latency_aware")
    assert pool.select() is c
    c.finish(100, True)
    assert pool.select() is b
    b.finish(30, True)


def test_smooth_weighted_round_robin_and_exclusion():
    a, b = Backend("a", "http://a", 2), Backend("b", "http://b", 1)
    pool = Pool([a, b], "weighted_round_robin")
    selected = []
    for _ in range(9):
        node = pool.select()
        assert node is not None
        selected.append(node.id)
        node.finish(10, True)
    assert selected.count("a") == 6
    b.disabled = True
    assert pool.select(excluded={"a"}) is None


def test_breaker_excludes_after_failures():
    b = Backend("a", "http://a")
    for _ in range(3):
        b.begin()
        b.finish(5, False)
    assert b.active == 0
    assert b.breaker == "open"
    assert not b.available


def test_latency_aware_explores_stale_backend():
    import time

    from app.fastapi_lb.routing import Backend, Pool

    a = Backend("a", "http://a", ewma=2, last_selected=time.monotonic())
    b = Backend("b", "http://b", ewma=200, last_selected=time.monotonic()-3)
    pool = Pool([a,b], "latency_aware")
    trace = {}
    selected = pool.select(trace=trace)
    assert selected is b
    assert trace["decision"] == "stale latency exploration"
    b.finish(2, True)
