import unittest

from src import taxonomy

TAXONOMY = [
    {"label": "buying-guides", "hints": ["buying guide", "how to choose"]},
    {"label": "strength-training-gear", "hints": ["kettlebell", "dumbbell"]},
    {"label": "misc", "hints": []},
]


class TestTagTitle(unittest.TestCase):
    def test_matches_hint(self):
        self.assertEqual(taxonomy.tag_title("Kettlebell buying guide 2026", TAXONOMY),
                          "buying-guides")

    def test_longest_hint_wins_specificity(self):
        tax = [
            {"label": "general-syllabus", "hints": ["syllabus"]},
            {"label": "pending-syllabus", "hints": ["pending syllabus"]},
        ]
        self.assertEqual(taxonomy.tag_title("Our pending syllabus update", tax),
                          "pending-syllabus")

    def test_no_match_returns_empty_string(self):
        self.assertEqual(taxonomy.tag_title("Completely unrelated title", TAXONOMY), "")

    def test_tie_break_goes_to_earlier_entry(self):
        tax = [
            {"label": "first", "hints": ["abc"]},
            {"label": "second", "hints": ["xyz"]},
        ]
        self.assertEqual(taxonomy.tag_title("abc and xyz both here", tax), "first")


class TestTagCandidates(unittest.TestCase):
    def test_rule_based_wins_over_session_label(self):
        candidates = [{"video_id": "v1", "title": "Best kettlebell for beginners"}]
        result = taxonomy.tag_candidates(candidates, TAXONOMY, {"v1": "misc"})
        self.assertEqual(result[0]["topic"], "strength-training-gear")

    def test_falls_back_to_session_label_when_no_rule_matches(self):
        candidates = [{"video_id": "v1", "title": "Unrelated title"}]
        result = taxonomy.tag_candidates(candidates, TAXONOMY, {"v1": "misc"})
        self.assertEqual(result[0]["topic"], "misc")

    def test_invalid_session_label_ignored(self):
        candidates = [{"video_id": "v1", "title": "Unrelated title"}]
        result = taxonomy.tag_candidates(candidates, TAXONOMY, {"v1": "not-a-real-label"})
        self.assertEqual(result[0]["topic"], "")

    def test_existing_topic_never_overwritten(self):
        candidates = [{"video_id": "v1", "title": "Best kettlebell", "topic": "already-set"}]
        result = taxonomy.tag_candidates(candidates, TAXONOMY)
        self.assertEqual(result[0]["topic"], "already-set")


if __name__ == "__main__":
    unittest.main()
