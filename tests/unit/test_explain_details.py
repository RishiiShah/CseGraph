from csegraph._core.retrieval.explain import build_reason_details


def test_reason_details_include_confidence_and_score():
    outgoing = {
        "tgt": [{"relation": "calls", "target_id": "callee", "confidence_tier": "INFERRED"}]
    }
    details = build_reason_details(
        reasons=["direct_call"],
        node_id="callee",
        target_id="tgt",
        score=2.5,
        outgoing=outgoing,
        incoming={},
    )
    assert details == [
        {
            "code": "direct_call",
            "confidence_tier": "INFERRED",
            "score_contribution": 2.5,
        }
    ]
