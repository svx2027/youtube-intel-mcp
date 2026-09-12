import unittest

from src import demand


class TestSupplyForKeyword(unittest.TestCase):
    def test_no_match_returns_zero(self):
        result = demand.supply_for_keyword("kettlebell workout",
                                            [{"title": "Treadmill review", "vph": 5.0}])
        self.assertEqual(result["matches"], 0)

    def test_counts_matches_and_tracks_freshest(self):
        candidates = [
            {"title": "Best kettlebell workout plan", "vph": 10.0, "hours_since": 5},
            {"title": "Kettlebell workout for beginners", "vph": 20.0, "hours_since": 40},
        ]
        result = demand.supply_for_keyword("kettlebell workout", candidates)
        self.assertEqual(result["matches"], 2)
        self.assertEqual(result["newest_hours"], 5)
        self.assertEqual(result["max_vph"], 20.0)

    def test_empty_keyword_tokens(self):
        result = demand.supply_for_keyword("the a of", [{"title": "x", "vph": 1.0}])
        self.assertEqual(result, {"matches": 0, "max_vph": 0.0, "newest_hours": None})


class TestBestKeywordMatch(unittest.TestCase):
    def test_single_word_keyword_can_match(self):
        kw, align = demand.best_keyword_match(
            "Kettlebell buying guide", {}, ["kettlebell"])
        self.assertEqual(kw, "kettlebell")
        self.assertGreater(align, 0)

    def test_no_overlap_returns_empty(self):
        kw, align = demand.best_keyword_match("Treadmill review", {}, ["kettlebell"])
        self.assertEqual(kw, "")
        self.assertEqual(align, 0.0)

    def test_prefers_vidiq_overall_score_when_available(self):
        kw_metrics = {"kettlebell workout": {"overall_score": 88, "competition": 20}}
        kw, align = demand.best_keyword_match(
            "Best kettlebell workout ever", kw_metrics, [])
        self.assertEqual(kw, "kettlebell workout")
        self.assertEqual(align, 88.0)


class TestAnnotateCandidates(unittest.TestCase):
    def test_flags_genuine_gap_only(self):
        candidates = [{"title": "Kettlebell workout plan", "kind": "video"}]
        vidiq = [{"keyword": "kettlebell workout", "overall_score": 80, "competition": 10}]
        demand.annotate_candidates(candidates, ["kettlebell workout"], vidiq)
        self.assertIn("DEMAND_GAP", candidates[0]["flags"])

    def test_high_competition_does_not_flag(self):
        candidates = [{"title": "Kettlebell workout plan", "kind": "video"}]
        vidiq = [{"keyword": "kettlebell workout", "overall_score": 80, "competition": 90}]
        demand.annotate_candidates(candidates, ["kettlebell workout"], vidiq)
        self.assertNotIn("DEMAND_GAP", candidates[0]["flags"])

    def test_alignment_alone_never_flags_without_vidiq_data(self):
        candidates = [{"title": "Kettlebell workout plan", "kind": "video"}]
        demand.annotate_candidates(candidates, ["kettlebell workout"])
        self.assertNotIn("DEMAND_GAP", candidates[0]["flags"])

    def test_non_video_kind_gets_zero_score(self):
        candidates = [{"title": "A poll", "kind": "post"}]
        demand.annotate_candidates(candidates, ["kettlebell"])
        self.assertEqual(candidates[0]["demand_gap_score"], 0.0)


class TestBuildDemandSection(unittest.TestCase):
    def test_stale_or_thin_true_when_no_supply(self):
        rows = demand.build_demand_section([], ["kettlebell workout"])
        self.assertTrue(rows[0]["stale_or_thin"])

    def test_ranked_by_gap_score_descending(self):
        candidates = [
            {"title": "Kettlebell workout plan", "kind": "video", "vph": 5.0, "hours_since": 1},
        ]
        vidiq = [
            {"keyword": "kettlebell workout", "overall_score": 90, "competition": 10},
            {"keyword": "treadmill review", "overall_score": 90, "competition": 10},
        ]
        rows = demand.build_demand_section(candidates, [], vidiq)
        scores = [r["gap_score"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # kettlebell has real supply matched above -> smaller gap than the
        # identically-scored, completely unserved treadmill keyword
        by_kw = {r["keyword"]: r["gap_score"] for r in rows}
        self.assertGreater(by_kw["treadmill review"], by_kw["kettlebell workout"])


if __name__ == "__main__":
    unittest.main()
