import unittest

from src import scoring


def _video(title, vph, comment_vph=0.0, like_vph=0.0, eng_rate=0.0,
           channel_id="c1", relation="direct", hours_since=10.0):
    return {
        "title": title, "vph": vph, "comment_vph": comment_vph,
        "like_vph": like_vph, "eng_rate": eng_rate, "channel_id": channel_id,
        "relation": relation, "hours_since": hours_since,
    }


class TestZscoresAndPctRank(unittest.TestCase):
    def test_zscores_empty(self):
        self.assertEqual(scoring.zscores([]), [])

    def test_zscores_zero_std_returns_all_zero(self):
        self.assertEqual(scoring.zscores([5.0, 5.0, 5.0]), [0.0, 0.0, 0.0])

    def test_zscores_known_values(self):
        z = scoring.zscores([1.0, 2.0, 3.0])
        self.assertAlmostEqual(z[1], 0.0, places=6)
        self.assertLess(z[0], 0)
        self.assertGreater(z[2], 0)

    def test_pct_rank_empty_sorted_is_zero(self):
        self.assertEqual(scoring.pct_rank(10, []), 0.0)

    def test_pct_rank_middle(self):
        sv = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(scoring.pct_rank(3.0, sv), 50.0)


class TestComputeOutliers(unittest.TestCase):
    def test_uses_baseline_when_present(self):
        videos = [_video("a", vph=100.0, channel_id="c1")]
        scoring.compute_outliers(videos, baselines={"c1": 50.0})
        self.assertEqual(videos[0]["outlier"], 2.0)
        self.assertEqual(videos[0]["outlier_source"], "baseline")

    def test_falls_back_to_same_run_median(self):
        videos = [
            _video("a", vph=10.0, channel_id="c1"),
            _video("b", vph=40.0, channel_id="c1"),
            _video("c", vph=100.0, channel_id="c1"),
        ]
        scoring.compute_outliers(videos)
        # median of [10, 40, 100] is 40 -> the 100-vph video is 2.5x
        self.assertEqual(videos[2]["outlier"], 2.5)
        self.assertEqual(videos[2]["outlier_source"], "same-run")

    def test_zero_median_defaults_to_one(self):
        videos = [_video("a", vph=0.0, channel_id="c1")]
        scoring.compute_outliers(videos)
        self.assertEqual(videos[0]["outlier"], 1.0)

    def test_display_cap_applied(self):
        videos = [_video("a", vph=10000.0, channel_id="c1")]
        scoring.compute_outliers(videos, baselines={"c1": 1.0}, outlier_display_cap=20.0)
        self.assertEqual(videos[0]["outlier"], 20.0)


class TestClustering(unittest.TestCase):
    def test_groups_by_rarest_shared_token(self):
        titles = [
            "Best kettlebell for beginners",
            "Kettlebell workout for beginners",
            "Treadmill buying guide 2026",
        ]
        clusters = scoring.cluster_titles(titles, anchor_tokens=["kettlebell", "treadmill"])
        labels = [c["label"] for c in clusters]
        self.assertTrue(any("kettlebell" in lbl for lbl in labels))
        self.assertTrue(any("treadmill" in lbl for lbl in labels))

    def test_ubiquitous_non_anchor_token_dropped(self):
        # "gear" appears in every title and is not an anchor -> should not
        # itself become the sole basis for clustering when a more specific
        # anchor token is present in some titles.
        titles = ["Best gear for lifting", "Best gear for running", "Best gear for yoga"]
        clusters = scoring.cluster_titles(titles, anchor_tokens=[], max_df=0.4)
        # all 3 titles collapse to singleton/leftover groups since their only
        # significant token ("gear") is corpus-ubiquitous and gets dropped
        total = sum(len(c["indices"]) for c in clusters)
        self.assertEqual(total, 3)

    def test_cluster_label_is_deterministic_regardless_of_set_order(self):
        titles = ["Alpha beta kettlebell gamma", "Alpha beta kettlebell delta"]
        c1 = scoring.cluster_titles(titles, anchor_tokens=["kettlebell"])
        c2 = scoring.cluster_titles(list(reversed(titles)), anchor_tokens=["kettlebell"])
        self.assertEqual(sorted(c["label"] for c in c1), sorted(c["label"] for c in c2))

    def test_empty_titles_list(self):
        self.assertEqual(scoring.cluster_titles([]), [])


class TestScoreCandidates(unittest.TestCase):
    def test_ranks_higher_vph_first(self):
        candidates = [
            _video("Slow video", vph=5.0),
            _video("Fast video", vph=500.0),
        ]
        result = scoring.score_candidates(candidates)
        self.assertEqual(result["candidates"][0]["title"], "Fast video")
        self.assertEqual(result["meta"]["n_candidates"], 2)

    def test_self_relation_zero_weight_ranks_last_by_default(self):
        candidates = [
            _video("Our own upload", vph=1000.0, relation="self", channel_id="me"),
            _video("Competitor upload", vph=10.0, relation="direct", channel_id="them"),
        ]
        result = scoring.score_candidates(candidates)
        scores = {c["title"]: c["opportunity_score"] for c in result["candidates"]}
        self.assertEqual(scores["Our own upload"], 0.0)
        self.assertGreater(scores["Competitor upload"], 0.0)

    def test_breakout_flag_fires_above_threshold(self):
        candidates = [
            _video("a", vph=10.0, channel_id="c1"),
            _video("b", vph=10.0, channel_id="c1"),
            _video("b2", vph=100.0, channel_id="c1"),
        ]
        result = scoring.score_candidates(candidates)
        top = result["candidates"][0]
        self.assertIn("BREAKOUT", top["flags"])

    def test_convergence_flag_needs_min_cluster_size(self):
        # All 3 titles are the same hook from 3 different channels: their
        # only non-generic, non-ubiquitous token is the anchor "kettlebell"
        # itself (the rest are corpus-ubiquitous and dropped before
        # clustering), so they land in one cluster together.
        candidates = [
            _video("Kettlebell prime day deal", vph=50.0, channel_id="c1", hours_since=1),
            _video("Kettlebell prime day deal", vph=50.0, channel_id="c2", hours_since=1),
            _video("Kettlebell prime day deal", vph=50.0, channel_id="c3", hours_since=1),
        ]
        result = scoring.score_candidates(
            candidates, config={"niche": {"cluster_anchor_tokens": ["kettlebell"]}})
        flagged = [c for c in result["candidates"] if "CONVERGENCE" in c["flags"]]
        self.assertEqual(len(flagged), 3)

    def test_convergence_flag_absent_when_titles_diverge(self):
        candidates = [
            _video("Kettlebell deal roundup", vph=50.0, channel_id="c1", hours_since=1),
            _video("Kettlebell deal guide", vph=50.0, channel_id="c2", hours_since=1),
            _video("Kettlebell deal alert", vph=50.0, channel_id="c3", hours_since=1),
        ]
        result = scoring.score_candidates(
            candidates, config={"niche": {"cluster_anchor_tokens": ["kettlebell"]}})
        flagged = [c for c in result["candidates"] if "CONVERGENCE" in c["flags"]]
        self.assertEqual(len(flagged), 0)

    def test_custom_weights_change_ranking(self):
        candidates = [
            _video("High vph low engagement", vph=1000.0, comment_vph=0.0),
            _video("Low vph high engagement", vph=1.0, comment_vph=50.0, channel_id="c2"),
        ]
        default_result = scoring.score_candidates(candidates)
        engagement_heavy = scoring.score_candidates(
            [dict(c) for c in candidates],
            config={"weights": {"vph": 0, "outlier": 0, "engagement": 100,
                                 "demand_gap": 0, "convergence": 0, "calendar": 0}},
        )
        self.assertEqual(default_result["candidates"][0]["title"], "High vph low engagement")
        self.assertEqual(engagement_heavy["candidates"][0]["title"], "Low vph high engagement")

    def test_empty_input(self):
        result = scoring.score_candidates([])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["meta"]["n_candidates"], 0)


if __name__ == "__main__":
    unittest.main()
