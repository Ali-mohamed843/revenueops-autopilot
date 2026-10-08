import pytest

from revenueops.cases.models import CaseType
from revenueops.policies import all_policies, parse, policies_for


def test_every_case_type_gets_policies_and_tags_are_real_case_types() -> None:
    for case_type in CaseType:
        assert policies_for(case_type), case_type
    for policy in all_policies():
        assert set(policy.applies_to) <= set(CaseType), policy.id


def test_rule_ids_are_unique_and_prefixed_by_their_policy() -> None:
    rules = [r for p in all_policies() for r in p.rules]
    assert len(rules) == len(set(rules))
    for p in all_policies():
        assert p.rules and all(r.startswith(p.id + "-") for r in p.rules), p.id


def test_parse() -> None:
    p = parse("---\nid: X\ntitle: Test\napplies_to: a, b\n---\n# Test\r\n- **X-1** One.\n- **X-2** Two.\n")
    assert (p.id, p.title, p.applies_to, p.rules) == ("X", "Test", ("a", "b"), ("X-1", "X-2"))
    with pytest.raises(ValueError, match="header"):
        parse("# no header")
    with pytest.raises(ValueError, match="missing applies_to"):
        parse("---\nid: X\ntitle: T\n---\nbody")
