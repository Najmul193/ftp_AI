"""The privacy gateway's pure rules: what may leave, and in what form."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.gateway import dlp, generalize, grounding
from app.ai.gateway.policy import (
    FieldPolicy, PolicyViolation, Tier, check_provider, payload_tier,
)
from app.ai.gateway.tokenizer import Entity, Vault

GULSHAN = Entity("BR", "101", "Gazipur Gulshan (101)", ("Gazipur Gulshan", "101"))
GEC = Entity("BR", "111", "Chattogram GEC (111)", ("Chattogram GEC", "GEC", "111"))


# --- tokenizer --------------------------------------------------------------- #

def test_token_is_stable_within_a_vault_and_round_trips():
    v = Vault()
    t1 = v.token("BR", "101", "Gazipur Gulshan (101)")
    assert v.token("BR", "101") == t1
    assert t1.startswith("BR_") and len(t1) == 6
    assert v.rehydrate(f"{t1} led the division") == "Gazipur Gulshan (101) led the division"


def test_tokens_are_unlinkable_across_vaults():
    # The same branch in two conversations must not share a token, or a
    # provider logging both could join them.
    seen = {Vault().token("BR", "101") for _ in range(40)}
    assert len(seen) > 30


def test_token_carries_nothing_of_the_identifier():
    v = Vault()
    tok = v.token("BR", "101", "Gazipur Gulshan (101)")
    assert "101" not in tok and "GUL" not in tok.upper()[3:]


def test_unknown_token_stays_masked():
    v = Vault()
    v.token("BR", "101", "Gazipur Gulshan (101)")
    assert v.rehydrate("BR_ZZZ fell") == "BR_ZZZ fell"


def test_mask_text_replaces_names_longest_first_and_codes_only_as_codes():
    v = Vault()
    v.register([GULSHAN, GEC])
    t_g, t_c = v.token("BR", "101"), v.token("BR", "111")
    out = v.mask_text("Why did Gazipur Gulshan fall while branch 111 grew by 101 crore?")
    assert "Gulshan" not in out and t_g in out
    assert f"branch {t_c}" in out
    # "101 crore" is an amount, not a branch code, and must survive.
    assert "101 crore" in out


def test_vault_persists():
    v = Vault()
    v.register([GULSHAN])
    tok = v.token("BR", "101")
    w = Vault.from_dict(v.to_dict())
    assert w.token("BR", "101") == tok
    assert w.rehydrate(tok) == "Gazipur Gulshan (101)"


# --- generalize -------------------------------------------------------------- #

@pytest.mark.parametrize("amount,expected", [
    (4_127_341_182.25, "413 cr"),
    (41_273_411.82, "4.13 cr"),
    (1_000_000, "10 lakh"),
    (-2_345_678_901, "-235 cr"),
    (0, "0 cr"),
])
def test_amounts_lose_their_fingerprint(amount, expected):
    assert generalize.crore(amount) == expected


def test_rates_and_bps():
    assert generalize.rate(Decimal("8.7864")) == "8.79%"
    assert generalize.bps(Decimal("0.6")) == "+60 bp"
    assert generalize.bps(Decimal("-0.255")) == "-26 bp"


def test_index_scales_to_100():
    assert generalize.index({"a": 50, "b": 200, "c": -100}) == {
        "a": Decimal("25.0"), "b": Decimal("100.0"), "c": Decimal("-50.0")}


# --- policy ------------------------------------------------------------------ #

def test_account_numbers_cannot_be_configured_to_leave():
    with pytest.raises(PolicyViolation) as e:
        FieldPolicy.from_dict({"actions": {"account_no": "pass"}})
    assert e.value.code == "hard_drop"
    assert FieldPolicy().action("account_no") == "drop"


def test_default_policy_is_aggregate_tier():
    p = FieldPolicy()
    assert p.bank_tier is Tier.AGGREGATE
    assert payload_tier({"instruction", "public"}, p) is Tier.PUBLIC
    assert payload_tier({"instruction", "bank"}, p) is Tier.AGGREGATE
    assert payload_tier({"user"}, p) is Tier.AGGREGATE


def test_clear_branch_names_raise_the_tier():
    p = FieldPolicy.from_dict({"actions": {"branch": "pass"}})
    assert p.bank_tier is Tier.RESTRICTED


def test_provider_tier_gate():
    check_provider(Tier.PUBLIC, Tier.PUBLIC)
    with pytest.raises(PolicyViolation) as e:
        check_provider(Tier.AGGREGATE, Tier.PUBLIC)
    assert e.value.code == "tier_exceeded"


def test_bad_policy_values_are_refused():
    with pytest.raises(PolicyViolation):
        FieldPolicy.from_dict({"actions": {"product_code": "pass"}})
    with pytest.raises(PolicyViolation):
        FieldPolicy.from_dict({"actions": {"nonsense": "pass"}})


# --- dlp --------------------------------------------------------------------- #

def test_dlp_blocks_a_leaked_branch_name_in_bank_text():
    hits = dlp.scan([("bank", "Gazipur Gulshan deposits 413 cr")],
                    known={"Gazipur Gulshan"})
    assert [h.rule for h in hits] == ["known_identifier"]
    assert "Gulshan" not in hits[0].hint     # the finding never logs the identifier


def test_dlp_ignores_place_names_in_public_news():
    hits = dlp.scan([("public", "Floods hit Gazipur Gulshan area")], known={"Gazipur Gulshan"})
    assert hits == []


def test_dlp_blocks_account_number_shapes_everywhere():
    for kind in ("public", "bank", "user", "instruction"):
        assert dlp.scan([(kind, "acct 2012050179781 balance")], known=()) != []
        assert dlp.scan([(kind, "acct 2012-0501-79781")], known=()) != []


def test_dlp_leaves_generalised_amounts_and_rates_alone():
    text = "NII 41.3 cr, cost of deposits 6.25%, +60 bp, 1,234 accounts, 2026-09-23"
    assert dlp.scan([("bank", text)], known={"Gazipur Gulshan"}) == []


def test_dlp_matches_numeric_codes_only_as_codes():
    assert dlp.scan([("bank", "grew 101 cr")], known=(), numeric_known={"101"}) == []
    assert dlp.scan([("bank", "branch 101 grew")], known=(), numeric_known={"101"}) != []


def test_dlp_catches_phone_and_email():
    assert dlp.scan([("user", "call 01712345678")], known=()) != []
    assert dlp.scan([("user", "mail a.b@bank.com.bd")], known=()) != []


def test_tokenised_text_passes_dlp():
    v = Vault()
    v.register([GULSHAN, GEC])
    masked = v.mask_text("Gazipur Gulshan and Chattogram GEC, branch 101")
    assert dlp.scan([("user", masked)], known=v.raw_spellings(),
                    numeric_known={"101", "111"}) == []


# --- grounding --------------------------------------------------------------- #

FACTS = [Decimal("8.79"), Decimal("8.19"), Decimal("41.3"), Decimal("6.25"), Decimal("1234")]


def test_supplied_numbers_verify():
    g = grounding.check("Call money averaged 8.79%, NII 41.3 cr across 1,234 accounts.", FACTS)
    assert g.ok and g.checked == 3


def test_derived_difference_and_bp_verify():
    g = grounding.check("Up 60 bp from 8.19% to 8.79% (0.6 pp).", FACTS)
    assert g.ok, g.unverified


def test_rounding_is_tolerated():
    assert grounding.check("NII of about 41 cr", FACTS).ok
    assert grounding.check("call money near 8.8%", FACTS).ok


def test_invented_number_is_flagged():
    g = grounding.check("NII rose 4.2% to 52.7 cr", FACTS)
    assert not g.ok and set(g.unverified) == {"4.2", "52.7"}


def test_counts_years_dates_and_tokens_are_not_claims():
    g = grounding.check("3 actions for BR_K7Q in 2026 (as of 2026-09-23), the 2nd time.", FACTS)
    assert g.ok and g.checked == 0


def test_bangla_digits_are_checked():
    assert grounding.check("কল মানি ৮.৭৯%", FACTS).ok
    assert not grounding.check("কল মানি ৯.৯৯%", FACTS).ok


def test_redact_removes_findings_but_keeps_the_rest():
    out = dlp.redact("user", "check 2012050179781 at Gazipur Gulshan, branch 101, 413 cr",
                     known={"Gazipur Gulshan"}, numeric_known={"101"})
    assert "2012050179781" not in out and "Gulshan" not in out and "branch 101" not in out
    assert "[REDACTED:account_number_shape]" in out and "413 cr" in out
    assert "branch [REDACTED:known_code]" in out


def test_redact_leaves_public_place_names():
    assert dlp.redact("public", "Floods in Gazipur Gulshan", known={"Gazipur Gulshan"}) \
        == "Floods in Gazipur Gulshan"


# --- short codes that are also words ------------------------------------------ #

NETROKONA = Entity("DIST", "NET", "Netrokona", ("Netrokona",))


def test_a_district_code_that_is_a_word_is_matched_only_in_capitals():
    # "NET" is Netrokona's code. "Net FTP profit" is not a leak; "NET" is.
    known = {"NET", "Netrokona"}
    assert dlp.scan([("bank", "Net FTP profit this week 1.07 cr")], known) == []
    assert dlp.scan([("bank", "net interest income")], known) == []
    assert [h.rule for h in dlp.scan([("bank", "district NET fell")], known)] == ["known_identifier"]
    assert [h.rule for h in dlp.scan([("bank", "netrokona fell")], known)] == ["known_identifier"]


def test_masking_leaves_the_word_and_takes_the_code():
    v = Vault()
    v.register([NETROKONA])
    tok = v.token("DIST", "NET")
    assert v.mask_text("why is net profit down?") == "why is net profit down?"
    assert v.mask_text("why is NET down?") == f"why is {tok} down?"
    assert v.mask_text("why is Netrokona down?") == f"why is {tok} down?"


def test_written_dates_are_not_claims():
    g = grounding.check("On 23 Sep the bill cleared at 8.32%, from Sep 16, 2026.",
                        [Decimal("8.32")])
    assert g.ok and g.checked == 1


def test_a_large_fact_set_does_not_verify_everything():
    # 150 facts would give 11,000 pairwise differences, dense enough to
    # "verify" an invented figure. Only differences on the same line count.
    text = "\n".join(f"- product {chr(65 + i % 26)}: benchmark {Decimal(800 + i) / 100}%, "
                     f"market {Decimal(8500 + 13 * i) / 1000}%" for i in range(75))
    facts = grounding.differences_by_line(text)
    # 1.23 is the gap between two different products' rates: not a claim
    # anything in the facts supports.
    assert not grounding.check("the gap is 1.23 points", facts).ok
    # Product 10's own benchmark-to-market gap is.
    assert grounding.check("product K: benchmark 8.1% against market 8.63%, a gap of 0.53",
                           facts).ok


def test_words_that_start_like_months_do_not_hide_numbers():
    # "market 8.63%" is not a date, and its number must still be checked.
    g = grounding.check("market 8.63%, marginal 9.1%, decent 7.7%", [Decimal("8.63")])
    assert g.checked == 3 and set(g.unverified) == {"9.1", "7.7"}


def test_dates_written_with_typographic_hyphens_are_still_dates():
    # gpt-oss writes "2026‑09‑23" with non-breaking hyphens; the 23 is a day.
    g = grounding.check("The period covered is 2026‑09‑03 to 2026‑09‑23, and 03 Sep‑23 Sep 2026; NIM 8.89%.",
                        [Decimal("8.89")])
    assert g.ok, g.unverified
