from market_data import MarketDataManager


def test_market_data_feed_health_and_tick_cache():
    manager = MarketDataManager(max_tick_age_seconds=2)
    manager._process_live_tick({
        "token": "123",
        "last_traded_price": 1000,
        "volume_traded": 10,
        "high_price": 1002,
        "low_price": 998,
        "open_price": 1000,
        "close_price": 1000,
    })

    tick = manager.get_live_tick("123")
    assert tick is not None
    assert tick["ltp"] == 10.0
    assert tick["cum_volume"] == 10

    health = manager.get_feed_health()
    assert health["connected"] is True or health["connected"] is False
    assert "last_tick_timestamp" in health
    assert "latency_ms" in health
