"""src/validation.py: the shape/type/size checks every server.py tool
wrapper runs before handing input to scoring.py/demand.py/taxonomy.py.
Each test names the exact malformed input and asserts the exact ValueError
message prefix, since the message is what an MCP caller actually sees
(see tests/test_server.py for the same checks wired through the real tool
functions and MCP dispatch, not just called directly here)."""
import unittest

from src import validation


class TestRequireList(unittest.TestCase):
    def test_accepts_list_within_cap(self):
        self.assertEqual(validation.require_list([1, 2], "x"), [1, 2])

    def test_rejects_non_list(self):
        with self.assertRaisesRegex(ValueError, "x must be a list, got dict"):
            validation.require_list({}, "x")

    def test_rejects_over_cap(self):
        with self.assertRaisesRegex(ValueError, r"x has 3 items, over the 2 limit"):
            validation.require_list([1, 2, 3], "x", max_items=2)

    def test_accepts_exactly_at_cap(self):
        validation.require_list([1, 2], "x", max_items=2)


class TestRequireOptionalDict(unittest.TestCase):
    def test_none_is_fine(self):
        self.assertIsNone(validation.require_optional_dict(None, "config"))

    def test_dict_passes_through(self):
        self.assertEqual(validation.require_optional_dict({"a": 1}, "config"), {"a": 1})

    def test_non_dict_rejected(self):
        with self.assertRaisesRegex(ValueError, "config must be an object"):
            validation.require_optional_dict([1, 2], "config")


class TestRequireNumber(unittest.TestCase):
    def test_int_and_float_accepted(self):
        self.assertEqual(validation.require_number(1, "n"), 1)
        self.assertEqual(validation.require_number(1.5, "n"), 1.5)

    def test_bool_rejected(self):
        # bool is a subclass of int in Python - explicitly excluded so a
        # stray True/False isn't silently treated as 1/0.
        with self.assertRaisesRegex(ValueError, "n must be a number, got bool"):
            validation.require_number(True, "n")

    def test_string_rejected(self):
        with self.assertRaisesRegex(ValueError, "n must be a number, got str"):
            validation.require_number("1.5", "n")


class TestValidateStringList(unittest.TestCase):
    def test_accepts_strings(self):
        self.assertEqual(validation.validate_string_list(["a", "b"], "titles"), ["a", "b"])

    def test_rejects_non_string_item(self):
        with self.assertRaisesRegex(ValueError, r"titles\[1\] must be a string, got int"):
            validation.validate_string_list(["a", 2], "titles")


class TestValidateCandidates(unittest.TestCase):
    def test_accepts_well_formed_batch(self):
        candidates = [{"title": "a", "vph": 10.0, "channel_id": "c1", "relation": "direct"}]
        self.assertEqual(validation.validate_candidates(candidates), candidates)

    def test_rejects_non_list(self):
        with self.assertRaisesRegex(ValueError, "candidates must be a list"):
            validation.validate_candidates({"title": "a"})

    def test_rejects_non_dict_item(self):
        with self.assertRaisesRegex(ValueError, r"candidates\[0\] must be an object"):
            validation.validate_candidates(["not a dict"])

    def test_rejects_wrong_type_numeric_field(self):
        with self.assertRaisesRegex(ValueError, r"candidates\[0\].vph must be a number, got str"):
            validation.validate_candidates([{"vph": "fast"}])

    def test_rejects_wrong_type_string_field(self):
        with self.assertRaisesRegex(ValueError,
                                     r"candidates\[0\].title must be a string, got int"):
            validation.validate_candidates([{"title": 123}])

    def test_rejects_invalid_relation(self):
        with self.assertRaisesRegex(ValueError, r"candidates\[0\].relation must be one of"):
            validation.validate_candidates([{"relation": "enemy"}])

    def test_rejects_non_list_flags(self):
        with self.assertRaisesRegex(ValueError, r"candidates\[0\].flags must be a list"):
            validation.validate_candidates([{"flags": "BREAKOUT"}])

    def test_missing_optional_fields_pass(self):
        # Every candidate field is optional at this layer - absence is not
        # an error, only the wrong type for a field that IS present.
        validation.validate_candidates([{}])

    def test_none_values_pass(self):
        validation.validate_candidates([{"vph": None, "title": None, "relation": None}])

    def test_rejects_over_cap(self):
        with self.assertRaisesRegex(ValueError, "over the 1 limit"):
            validation.validate_candidates([{}, {}], max_items=1)


class TestValidateVidiqKeywords(unittest.TestCase):
    def test_none_passes(self):
        self.assertIsNone(validation.validate_vidiq_keywords(None))

    def test_list_of_dicts_passes(self):
        items = [{"keyword": "kettlebell", "volume": 100}]
        self.assertEqual(validation.validate_vidiq_keywords(items), items)

    def test_rejects_non_dict_item(self):
        with self.assertRaisesRegex(ValueError, r"vidiq_keywords\[0\] must be an object"):
            validation.validate_vidiq_keywords(["kettlebell"])


class TestValidateTaxonomy(unittest.TestCase):
    def test_valid_taxonomy_passes(self):
        tax = [{"label": "misc", "hints": ["x"]}]
        self.assertEqual(validation.validate_taxonomy(tax), tax)

    def test_missing_label_rejected(self):
        with self.assertRaisesRegex(ValueError,
                                     r"taxonomy_labels\[0\].label must be a non-empty string"):
            validation.validate_taxonomy([{"hints": ["x"]}])

    def test_empty_label_rejected(self):
        with self.assertRaisesRegex(ValueError, r"taxonomy_labels\[0\].label"):
            validation.validate_taxonomy([{"label": "", "hints": []}])

    def test_non_string_hints_rejected(self):
        with self.assertRaisesRegex(ValueError, r"taxonomy_labels\[0\].hints"):
            validation.validate_taxonomy([{"label": "misc", "hints": [1, 2]}])

    def test_missing_hints_defaults_to_empty_list(self):
        validation.validate_taxonomy([{"label": "misc"}])


class TestValidateStrStrMap(unittest.TestCase):
    def test_none_passes(self):
        self.assertIsNone(validation.validate_str_str_map(None, "session_labels"))

    def test_valid_map_passes(self):
        self.assertEqual(validation.validate_str_str_map({"v1": "misc"}, "session_labels"),
                          {"v1": "misc"})

    def test_non_dict_rejected(self):
        with self.assertRaisesRegex(ValueError, "session_labels must be an object"):
            validation.validate_str_str_map(["v1"], "session_labels")

    def test_non_string_value_rejected(self):
        with self.assertRaisesRegex(ValueError, "session_labels must map string"):
            validation.validate_str_str_map({"v1": 2}, "session_labels")


class TestValidateStrNumberMap(unittest.TestCase):
    def test_none_passes(self):
        self.assertIsNone(validation.validate_str_number_map(None, "baselines"))

    def test_valid_map_passes(self):
        self.assertEqual(validation.validate_str_number_map({"c1": 10.5}, "baselines"),
                          {"c1": 10.5})

    def test_non_dict_rejected(self):
        with self.assertRaisesRegex(ValueError, "baselines must be an object"):
            validation.validate_str_number_map([1, 2], "baselines")

    def test_non_numeric_value_rejected(self):
        with self.assertRaisesRegex(ValueError, r"baselines\['c1'\] must be a number"):
            validation.validate_str_number_map({"c1": "ten"}, "baselines")


if __name__ == "__main__":
    unittest.main()
