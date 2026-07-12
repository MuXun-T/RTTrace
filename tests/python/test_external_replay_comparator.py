from parser.external_replay_comparator import compare
def test_exact_and_stable_mismatch():
 assert compare({"a":1},{"a":1}).matched
 assert compare({"b":1,"a":2},{"b":2,"c":3}).mismatches==("a","b","c")
