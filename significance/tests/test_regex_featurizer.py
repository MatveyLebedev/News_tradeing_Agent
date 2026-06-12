import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regex_featurizer import RegexFeaturizer  # noqa: E402

rf = RegexFeaturizer()


def test_feature_names_stable():
    names = rf.feature_names
    assert len(names) == 30
    # transform output keys match declared feature names exactly
    f = rf.transform("test")
    assert set(f.keys()) == set(names)


def test_empty_and_none_safe():
    for f in (rf.transform(""), rf.transform(None)):
        assert all(v == 0.0 for v in f.values())


def test_earnings_and_percent():
    f = rf.transform("Nvidia Surges 12% After Blowout Earnings, Raises Guidance")
    assert f["evt_earnings_n"] >= 1
    assert f["max_pct"] == 12.0
    assert f["has_pct"] == 1.0
    assert f["dir_pos_n"] >= 1 and f["dir_net"] > 0


def test_mna_and_dollar():
    f = rf.transform("Apple to Acquire AI Startup in $2 Billion Deal")
    assert f["evt_mna_n"] >= 1
    assert f["has_dollar"] == 1.0 and f["has_big_amount"] == 1.0


def test_legal_and_negative_direction():
    f = rf.transform("SEC Probes Tesla Over Claims; Shares Tumble")
    assert f["evt_legal_reg_n"] >= 1
    assert f["dir_neg_n"] >= 1 and f["dir_net"] < 0


def test_macro_detected():
    f = rf.transform("Fed Holds Rates Steady as Inflation Cools")
    assert f["evt_macro_n"] >= 1


def test_counts_are_numeric_and_nonnegative():
    f = rf.transform("Layoffs and lawsuit: CEO resigns amid SEC probe, stock plunges 30%")
    assert all(isinstance(v, float) and v >= 0 for k, v in f.items() if k != "dir_net")
    assert f["max_pct"] == 30.0
