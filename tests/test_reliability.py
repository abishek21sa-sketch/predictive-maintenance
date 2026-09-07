from pdm_intelligence.reliability.analysis import fit_reliability, weibull_hazard


def test_reliability_metrics_are_physical():
    r = fit_reliability([100, 120, 140, 160, 180], [6, 7, 8, 9, 10])
    assert r.mtbf_cycles == 140
    assert 0 < r.availability < 1
    assert r.weibull_shape > 0 and r.weibull_scale > 0
    h = weibull_hazard([50,100], r.weibull_shape, r.weibull_scale)
    assert (h > 0).all()
