from pdm_intelligence.storage.registry import AssetRegistry, FeatureStore


def test_registry_and_feature_store(tmp_path):
    db=tmp_path/'pdm.db'
    r=AssetRegistry(db); r.upsert(1, metadata={"fleet":"FD001"})
    assert r.list_assets()[0]["metadata"]["fleet"] == "FD001"
    f=FeatureStore(db); f.put(1,10,{"sensor_02_roll_mean":642.1})
    assert f.get(1,10)["sensor_02_roll_mean"] == 642.1
