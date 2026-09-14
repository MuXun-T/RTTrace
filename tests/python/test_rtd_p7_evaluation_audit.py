import json
def test_json_round_trip():
    assert json.loads(json.dumps({'observer_truth': False}))['observer_truth'] is False
