"""
RecurrenceParser / RecurrenceCalculator 循环规则解析与计算测试
"""
import pytest
from datetime import date

from project.apps.reconciliation.services.recurrence import (
    RecurrenceParseError,
    RecurrenceParser,
    RecurrenceCalculator,
)


# DSL 文本 → 期望的 RRULE 字符串，以及需要额外校验的字段
DSL_CASES = [
    ('every 3 days', 'FREQ=DAILY;INTERVAL=3',
     {'freq': 'DAILY', 'interval': 3}),
    ('every day', 'FREQ=DAILY',
     {'freq': 'DAILY', 'interval': 1}),
    ('every 10 days when done', 'FREQ=DAILY;INTERVAL=10',
     {'freq': 'DAILY', 'interval': 10, 'basis': 'completed'}),
    ('every weekday', 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR',
     {'freq': 'WEEKLY', 'byweekday': ['MO', 'TU', 'WE', 'TH', 'FR']}),
    ('every week', 'FREQ=WEEKLY',
     {'freq': 'WEEKLY', 'interval': 1}),
    ('every week on Sunday', 'FREQ=WEEKLY;BYDAY=SU',
     {'freq': 'WEEKLY', 'byweekday': ['SU']}),
    ('every week on Tuesday, Friday', 'FREQ=WEEKLY;BYDAY=TU,FR',
     {'freq': 'WEEKLY', 'byweekday': ['TU', 'FR']}),
    ('every 2 weeks', 'FREQ=WEEKLY;INTERVAL=2',
     {'freq': 'WEEKLY', 'interval': 2}),
    ('every 3 weeks on Friday', 'FREQ=WEEKLY;INTERVAL=3;BYDAY=FR',
     {'freq': 'WEEKLY', 'interval': 3, 'byweekday': ['FR']}),
    ('every 2 months', 'FREQ=MONTHLY;INTERVAL=2',
     {'freq': 'MONTHLY', 'interval': 2}),
    ('every month on the 1st', 'FREQ=MONTHLY;BYMONTHDAY=1',
     {'freq': 'MONTHLY', 'bymonthday': [1]}),
    ('every month on the last', 'FREQ=MONTHLY;BYMONTHDAY=-1',
     {'freq': 'MONTHLY', 'bymonthday': [-1]}),
    ('every month on the last Friday', 'FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1',
     {'freq': 'MONTHLY', 'byweekday': ['FR'], 'bysetpos': [-1]}),
    ('every month on the 2nd last Friday', 'FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-2',
     {'freq': 'MONTHLY', 'byweekday': ['FR'], 'bysetpos': [-2]}),
    ('every 6 months on the 2nd Wednesday', 'FREQ=MONTHLY;INTERVAL=6;BYDAY=WE;BYSETPOS=2',
     {'freq': 'MONTHLY', 'interval': 6, 'byweekday': ['WE'], 'bysetpos': [2]}),
    ('every January on the 15th', 'FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=15',
     {'freq': 'YEARLY', 'bymonth': [1], 'bymonthday': [15]}),
    ('every February on the last', 'FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=-1',
     {'freq': 'YEARLY', 'bymonth': [2], 'bymonthday': [-1]}),
    ('every April and December on the 1st and 24th', 'FREQ=YEARLY;BYMONTH=4,12;BYMONTHDAY=1,24',
     {'freq': 'YEARLY', 'bymonth': [4, 12], 'bymonthday': [1, 24]}),
    ('every year', 'FREQ=YEARLY',
     {'freq': 'YEARLY', 'interval': 1}),
]


@pytest.mark.parametrize('text,expected_rrule,fields', DSL_CASES)
def test_parse_rrule_string_and_fields(text, expected_rrule, fields):
    """覆盖全部 19 条 DSL：RRULE 字符串与关键字段"""
    spec = RecurrenceParser.parse(text)
    assert spec.to_rrule_string() == expected_rrule
    for key, value in fields.items():
        assert getattr(spec, key) == value, f'{text} 的 {key} 不正确'


def test_parse_is_case_insensitive_and_whitespace_tolerant():
    """大小写不敏感、容忍多余空白"""
    spec = RecurrenceParser.parse('  EVERY   3   DAYS  ')
    assert spec.to_rrule_string() == 'FREQ=DAILY;INTERVAL=3'
    assert spec.freq == 'DAILY'
    assert spec.interval == 3


def test_when_done_sets_completed_basis():
    """when done 后缀 → basis=completed"""
    spec = RecurrenceParser.parse('every 10 days when done')
    assert spec.basis == 'completed'


def test_default_basis_is_scheduled():
    """未带 when done → basis=scheduled"""
    spec = RecurrenceParser.parse('every 3 days')
    assert spec.basis == 'scheduled'


def test_to_rrule_kwargs_mapping():
    """to_rrule_kwargs 只映射非空字段"""
    from dateutil.rrule import rrule, MONTHLY, FR

    spec = RecurrenceParser.parse('every month on the last Friday')
    kwargs = spec.to_rrule_kwargs()
    assert kwargs['freq'] == MONTHLY
    assert kwargs['interval'] == 1
    assert kwargs['byweekday'] == [FR]
    assert kwargs['bysetpos'] == [-1]
    assert 'bymonthday' not in kwargs
    assert 'bymonth' not in kwargs

    # 生成的 rrule 可正常使用
    rule = rrule(dtstart=__import__('datetime').datetime(2026, 1, 1), **kwargs)
    assert rule.after(__import__('datetime').datetime(2026, 1, 1)) is not None


DESCRIBE_CASES = [
    ('every 3 days', '每 3 天'),
    ('every weekday', '每个工作日'),
    ('every week on Sunday', '每周日'),
    ('every week on Tuesday, Friday', '每周二、周五'),
    ('every 2 weeks', '每 2 周'),
    ('every month on the last', '每月最后一天'),
    ('every month on the last Friday', '每月最后一个星期五'),
    ('every month on the 2nd last Friday', '每月倒数第二个星期五'),
    ('every 6 months on the 2nd Wednesday', '每 6 个月的第二个星期三'),
    ('every January on the 15th', '每年 1 月 15 日'),
    ('every April and December on the 1st and 24th', '每年 4 月 1 日、24 日与 12 月 1 日、24 日'),
    ('every 2 years', '每 2 年'),
    ('every 3 weeks on Monday, Tuesday, Wednesday, Thursday, Friday', '每 3 周的工作日'),
    ('every January on the last Friday', '每年 1 月最后一个星期五'),
    ('every month on the 1st and last', '每月 1 日、最后一天'),
    ('every 2 months on the 1st', '每 2 个月的 1 日'),
    ('every week when done', '每周（完成后起算）'),
]


@pytest.mark.parametrize('text,expected_substring', DESCRIBE_CASES)
def test_describe_contains_expected_substring(text, expected_substring):
    """describe() 中文描述包含期望子串"""
    assert expected_substring in RecurrenceParser.parse(text).describe()


@pytest.mark.parametrize('text', ['', '   ', 'every foo', 'when done', 'foo', 'every'])
def test_parse_error_negative_cases(text):
    """空串 / 无法识别 / 单独的 when done → RecurrenceParseError"""
    with pytest.raises(RecurrenceParseError):
        RecurrenceParser.parse(text)


def test_next_occurrence_monthly_first():
    """每月 1 号：1 月 15 日之后 → 2 月 1 日"""
    spec = RecurrenceParser.parse('every month on the 1st')
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 1), date(2026, 1, 15)
    ) == date(2026, 2, 1)


def test_next_occurrence_last_friday_of_month():
    """每月最后一个星期五：2026-01-01 之后 → 2026-01-30"""
    spec = RecurrenceParser.parse('every month on the last Friday')
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 1), date(2026, 1, 1)
    ) == date(2026, 1, 30)


def test_next_occurrence_is_strictly_after():
    """next_occurrence 严格晚于 after（相等时取下一次）"""
    spec = RecurrenceParser.parse('every week on Monday')
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 1), date(2026, 1, 5)
    ) == date(2026, 1, 12)


def test_next_occurrences_count_and_increasing():
    """next_occurrences 返回 count 个严格递增的日期"""
    spec = RecurrenceParser.parse('every week on Monday')
    result = RecurrenceCalculator.next_occurrences(
        spec, date(2026, 1, 1), date(2026, 1, 1), 3
    )
    assert len(result) == 3
    assert all(result[i] < result[i + 1] for i in range(len(result) - 1))
    assert result == [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19)]


def test_next_occurrence_monthly_clamps_to_month_end():
    """每 N 个月未指定日：月末日期截断到目标月末，而不是跳过该月"""
    spec = RecurrenceParser.parse('every month')
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 31), date(2026, 1, 31)
    ) == date(2026, 2, 28)
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 31), date(2026, 2, 28)
    ) == date(2026, 3, 31)
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 31), date(2026, 3, 31)
    ) == date(2026, 4, 30)


def test_next_occurrences_monthly_clamps_to_month_end():
    """每 N 个月：连续预览保持在月末，不丢月份"""
    spec = RecurrenceParser.parse('every month')
    assert RecurrenceCalculator.next_occurrences(
        spec, date(2026, 1, 31), date(2026, 1, 31), 3
    ) == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_next_occurrence_monthly_interval_keeps_anchor():
    """每 2 个月：跨月依然以起始日为锚点，不因月末截断累积漂移"""
    spec = RecurrenceParser.parse('every 2 months')
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 31), date(2026, 1, 31)
    ) == date(2026, 3, 31)
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 31), date(2026, 3, 31)
    ) == date(2026, 5, 31)
    assert RecurrenceCalculator.first_on_or_after(
        spec, date(2026, 1, 31)
    ) == date(2026, 1, 31)


def test_impossible_rule_returns_none():
    """不可满足的规则（每年 2 月 30 日）→ None，而非死循环"""
    spec = RecurrenceParser.parse('every February on the 30th')
    assert spec.to_rrule_string() == 'FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30'
    assert RecurrenceCalculator.next_occurrence(
        spec, date(2026, 1, 1), date(2026, 1, 1)
    ) is None
    assert RecurrenceCalculator.next_occurrences(
        spec, date(2026, 1, 1), date(2026, 1, 1), 3
    ) == []
