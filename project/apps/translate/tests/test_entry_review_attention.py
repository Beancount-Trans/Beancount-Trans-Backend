"""条目审核关注度判定测试。

关注度只看两件事：账户是否落到兜底账户，条目结构是否能解析。
"""
import pytest

from project.apps.translate.services.entry_review_attention import (
    REASON_ASSET_FALLBACK,
    REASON_BALANCE_FALLBACK,
    REASON_CATEGORY_FALLBACK,
    REASON_UNPARSABLE,
    annotate_entries,
    annotate_entry,
    classify_entry,
    count_needs_review,
    extract_posting_accounts,
)


def _entry(text: str):
    return {'uuid': 'entry-1', 'formatted': text, 'edited_formatted': text}


def _transaction(*postings: str) -> str:
    lines = ['2026-01-01 * "测试商户" "测试备注"', '    time: "12:00:00"']
    lines.extend(f'    {posting}' for posting in postings)
    return '\n'.join(lines)


def test_extract_posting_accounts_ignores_metadata():
    accounts = extract_posting_accounts(
        _transaction('Expenses:Food 10.00 CNY', 'Assets:Alipay -10.00 CNY')
    )

    assert accounts == ['Expenses:Food', 'Assets:Alipay']


def test_normal_entry_is_auto_passed():
    entry = _entry(_transaction('Expenses:Food 10.00 CNY', 'Assets:Alipay -10.00 CNY'))

    needs_review, reason, account = classify_entry(entry)

    assert needs_review is False
    assert reason is None
    assert account is None


def test_asset_fallback_needs_review():
    entry = _entry(_transaction('Expenses:Food 10.00 CNY', 'Assets:Other -10.00 CNY'))

    needs_review, reason, account = classify_entry(entry)

    assert needs_review is True
    assert reason == REASON_ASSET_FALLBACK
    assert account == 'Assets:Other'


@pytest.mark.parametrize('category', ['Expenses:Other', 'Income:Other'])
def test_category_fallback_needs_review(category):
    entry = _entry(_transaction(f'{category} 10.00 CNY', 'Assets:Alipay -10.00 CNY'))

    needs_review, reason, account = classify_entry(entry)

    assert needs_review is True
    assert reason == REASON_CATEGORY_FALLBACK
    assert account == category


def test_configured_balance_fallback_needs_review():
    entry = _entry(_transaction('Expenses:Food 10.00 CNY', 'Equity:Adjustments -10.00 CNY'))

    needs_review, reason, account = classify_entry(entry, 'Equity:Adjustments')

    assert needs_review is True
    assert reason == REASON_BALANCE_FALLBACK
    assert account == 'Equity:Adjustments'


def test_custom_balance_fallback_needs_review():
    entry = _entry(_transaction('Expenses:Food 10.00 CNY', 'Equity:Unknown -10.00 CNY'))

    needs_review, reason, _ = classify_entry(entry, 'Equity:Unknown')

    assert needs_review is True
    assert reason == REASON_BALANCE_FALLBACK


def test_account_prefix_boundary_is_respected():
    """Assets:Otherwise 不是兜底账户，避免前缀误判。"""
    entry = _entry(
        _transaction('Expenses:Food 10.00 CNY', 'Assets:Otherwise -10.00 CNY')
    )

    needs_review, _, _ = classify_entry(entry)

    assert needs_review is False


@pytest.mark.parametrize(
    'text',
    [
        '',
        '2026-01-01 * "只有头行" "没有过账行"',
        _transaction('Expenses:Food 10.00 CNY'),
        (
            '2026-01-01 * "第一条" "备注"\n'
            '    Expenses:Food 10.00 CNY\n'
            '    Assets:Alipay -10.00 CNY\n'
            '2026-01-02 * "第二条" "备注"\n'
            '    Expenses:Food 20.00 CNY\n'
            '    Assets:Alipay -20.00 CNY'
        ),
    ],
)
def test_unparsable_entries_need_review(text):
    needs_review, reason, account = classify_entry(_entry(text))

    assert needs_review is True
    assert reason == REASON_UNPARSABLE
    assert account is None


def test_annotate_entry_does_not_mutate_source():
    source = _entry(_transaction('Expenses:Other 10.00 CNY', 'Assets:Alipay -10.00 CNY'))

    annotated = annotate_entry(source)

    assert annotated is not source
    assert annotated['needs_review'] is True
    assert annotated['review_reason'] == REASON_CATEGORY_FALLBACK
    assert annotated['review_account'] == 'Expenses:Other'
    assert 'needs_review' not in source


def test_count_needs_review_and_annotate_entries():
    entries = [
        _entry(_transaction('Expenses:Food 10.00 CNY', 'Assets:Alipay -10.00 CNY')),
        _entry(_transaction('Expenses:Other 10.00 CNY', 'Assets:Alipay -10.00 CNY')),
    ]

    annotated = annotate_entries(entries, 'Equity:Adjustments')

    assert [item['needs_review'] for item in annotated] == [False, True]
    assert count_needs_review(entries, 'Equity:Adjustments') == 1
