"""
循环规则解析与计算服务

提供受控自然语言（英文 DSL）循环规则的解析（RecurrenceParser）、
规则表示（RecurrenceSpec）与基于 python-dateutil 的日期计算（RecurrenceCalculator）。

DSL 示例：
    every 3 days                    → 每 3 天
    every week on Tuesday, Friday   → 每周二、周五
    every month on the last Friday  → 每月最后一个星期五
    every April and December on the 1st and 24th
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta
from dateutil.rrule import (
    rrule, DAILY, WEEKLY, MONTHLY, YEARLY,
    MO, TU, WE, TH, FR, SA, SU,
)


class RecurrenceParseError(Exception):
    """Raised when DSL text cannot be parsed."""


# 两字母星期代码 → dateutil 常量
_WEEKDAY_MAP = {
    'MO': MO, 'TU': TU, 'WE': WE, 'TH': TH, 'FR': FR, 'SA': SA, 'SU': SU,
}

# 星期长名 / 三字母缩写 → 两字母代码
_WEEKDAY_NAMES = {
    'monday': 'MO', 'mon': 'MO',
    'tuesday': 'TU', 'tue': 'TU',
    'wednesday': 'WE', 'wed': 'WE',
    'thursday': 'TH', 'thu': 'TH',
    'friday': 'FR', 'fri': 'FR',
    'saturday': 'SA', 'sat': 'SA',
    'sunday': 'SU', 'sun': 'SU',
}

# 星期中文简称（周X）与长称，用于 describe()
_WEEKDAY_CN_SHORT = {
    'MO': '周一', 'TU': '周二', 'WE': '周三', 'TH': '周四',
    'FR': '周五', 'SA': '周六', 'SU': '周日',
}
_WEEKDAY_CN_LONG = {
    'MO': '星期一', 'TU': '星期二', 'WE': '星期三', 'TH': '星期四',
    'FR': '星期五', 'SA': '星期六', 'SU': '星期日',
}

# 工作日集合（周一至周五）
_WORKDAYS = ['MO', 'TU', 'WE', 'TH', 'FR']

# 月份名 / 三字母缩写 → 数字
_MONTH_NAMES = {
    'january': 1, 'jan': 1,
    'february': 2, 'feb': 2,
    'march': 3, 'mar': 3,
    'april': 4, 'apr': 4,
    'may': 5,
    'june': 6, 'jun': 6,
    'july': 7, 'jul': 7,
    'august': 8, 'aug': 8,
    'september': 9, 'sep': 9, 'sept': 9,
    'october': 10, 'oct': 10,
    'november': 11, 'nov': 11,
    'december': 12, 'dec': 12,
}

# 频率字符串 → dateutil 常量
_FREQ_MAP = {
    'DAILY': DAILY, 'WEEKLY': WEEKLY, 'MONTHLY': MONTHLY, 'YEARLY': YEARLY,
}

# 单位 → 频率
_UNIT_TO_FREQ = {
    'day': 'DAILY', 'days': 'DAILY',
    'week': 'WEEKLY', 'weeks': 'WEEKLY',
    'month': 'MONTHLY', 'months': 'MONTHLY',
    'year': 'YEARLY', 'years': 'YEARLY',
}

# 序数词匹配（1st / 2nd / 3rd / 4th ...）
_ORDINAL_RE = re.compile(r'^(\d+)(?:st|nd|rd|th)$')


def _cn_num(n: int) -> str:
    """把小的正整数转成中文数字（用于 describe 的“第 N 个”）"""
    n = abs(int(n))
    if n <= 10:
        return '零一二三四五六七八九十'[n]
    if n < 20:
        return '十' + '零一二三四五六七八九'[n % 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return '零一二三四五六七八九'[tens] + '十' + (
            '零一二三四五六七八九'[ones] if ones else ''
        )
    return str(n)


@dataclass
class RecurrenceSpec:
    """循环规则的结构化表示"""

    freq: str                                   # 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'YEARLY'
    interval: int = 1
    byweekday: list[str] | None = None          # ['MO', 'TU', ...]
    bymonthday: list[int] | None = None         # [1, 24] / [-1]（最后一天）
    bymonth: list[int] | None = None            # 1..12
    bysetpos: list[int] | None = None           # [-1] 最后 / [2] 第二个
    basis: str = 'scheduled'                    # 'scheduled' | 'completed'

    def to_rrule_kwargs(self) -> dict:
        """转换为 dateutil.rrule.rrule 的关键字参数（忽略 None 字段）"""
        kwargs = {'freq': _FREQ_MAP[self.freq], 'interval': self.interval}
        if self.byweekday:
            kwargs['byweekday'] = [_WEEKDAY_MAP[d] for d in self.byweekday]
        if self.bymonthday:
            kwargs['bymonthday'] = list(self.bymonthday)
        if self.bymonth:
            kwargs['bymonth'] = list(self.bymonth)
        if self.bysetpos:
            kwargs['bysetpos'] = list(self.bysetpos)
        return kwargs

    def to_rrule_string(self) -> str:
        """规范化的大写 RRULE 主体（不含 RRULE: 前缀）"""
        parts = [f'FREQ={self.freq}']
        if self.interval != 1:
            parts.append(f'INTERVAL={self.interval}')
        if self.bymonth:
            parts.append('BYMONTH=' + ','.join(str(m) for m in self.bymonth))
        if self.bymonthday:
            parts.append('BYMONTHDAY=' + ','.join(str(d) for d in self.bymonthday))
        if self.byweekday:
            parts.append('BYDAY=' + ','.join(self.byweekday))
        if self.bysetpos:
            parts.append('BYSETPOS=' + ','.join(str(p) for p in self.bysetpos))
        return ';'.join(parts)

    def describe(self) -> str:
        """生成人类可读的中文描述（覆盖解析器可产出的全部字段）"""
        if self.freq == 'DAILY':
            text = self._describe_daily()
        elif self.freq == 'WEEKLY':
            text = self._describe_weekly()
        elif self.freq == 'MONTHLY':
            text = self._describe_monthly()
        else:
            text = self._describe_yearly()
        # when done 不改变 RRULE，但需在描述里体现「完成后起算」基准
        if self.basis == 'completed':
            text += '（完成后起算）'
        return text

    # ---- describe 内部实现 ----

    def _weekday_text(self) -> str:
        """byweekday 的中文；带 bysetpos 时含位置，如「最后一个星期五」"""
        if self.bysetpos:
            pos = self.bysetpos[0]
            if pos == -1:
                pos_text = '最后一个'
            elif pos < 0:
                pos_text = f'倒数第{_cn_num(-pos)}个'
            else:
                pos_text = f'第{_cn_num(pos)}个'
            return pos_text + _WEEKDAY_CN_LONG[self.byweekday[0]]
        return '、'.join(_WEEKDAY_CN_LONG[d] for d in self.byweekday)

    def _bymonthday_text(self) -> str:
        """bymonthday 的中文（-1 → 最后一天，其他负数 → 倒数第 N 天）"""
        return '、'.join(
            '最后一天' if d == -1 else (f'倒数第{_cn_num(-d)}天' if d < 0 else f'{d} 日')
            for d in self.bymonthday
        )

    def _describe_daily(self) -> str:
        base = '每天' if self.interval == 1 else f'每 {self.interval} 天'
        # 单位驱动规则也可能挂 on 子句（如 every day on Monday）
        extras = []
        if self.byweekday:
            extras.append(self._weekday_text())
        if self.bymonthday:
            extras.append(self._bymonthday_text())
        return f'{base}（{"、".join(extras)}）' if extras else base

    def _describe_weekly(self) -> str:
        if self.interval == 1:
            if self.byweekday == _WORKDAYS:
                base = '每个工作日'
            elif self.byweekday:
                base = '每' + '、'.join(_WEEKDAY_CN_SHORT[d] for d in self.byweekday)
            else:
                base = '每周'
        else:
            base = f'每 {self.interval} 周'
            if self.byweekday == _WORKDAYS:
                base += '的工作日'
            elif self.byweekday:
                base += '的' + '、'.join(_WEEKDAY_CN_SHORT[d] for d in self.byweekday)
        # 周规则上挂的 bymonthday（如 every week on the last）
        if self.bymonthday:
            base += f'（每月{self._bymonthday_text()}）'
        return base

    def _describe_monthly(self) -> str:
        base = '每月' if self.interval == 1 else f'每 {self.interval} 个月'
        if self.byweekday:
            if self.bysetpos:
                # interval > 1 时用“的”连接，如“每 6 个月的第二个星期三”
                return f'{base}{"" if self.interval == 1 else "的"}{self._weekday_text()}'
            # 无位置：BYDAY 列表（如 every month on Monday）
            wd = '、'.join(_WEEKDAY_CN_SHORT[d] for d in self.byweekday)
            return f'{base}（{wd}）'
        if self.bymonthday:
            days = self._bymonthday_text()
            if self.interval == 1:
                # 「最后一天」前不加空格，「N 日」前加空格
                sep = '' if days.startswith(('最后', '倒数')) else ' '
                return f'{base}{sep}{days}'
            sep = '的' if days.startswith(('最后', '倒数')) else '的 '
            return f'{base}{sep}{days}'
        return base

    def _describe_yearly(self) -> str:
        base = '每年' if self.interval == 1 else f'每 {self.interval} 年'
        if self.bymonth and self.bymonthday:
            days = self._bymonthday_text()
            # “最后一天”前不加空格，“N 日”前加空格，如“2 月最后一天”“4 月 1 日”
            sep = '' if days.startswith(('最后', '倒数')) else ' '
            month_texts = [f'{m} 月{sep}{days}' for m in self.bymonth]
            return f'{base} ' + '与 '.join(month_texts)
        if self.bymonth and self.byweekday:
            wd = self._weekday_text()
            month_texts = [f'{m} 月{wd}' for m in self.bymonth]
            return f'{base} ' + '与 '.join(month_texts)
        if self.bymonth:
            return f'{base} ' + '、'.join(f'{m} 月' for m in self.bymonth)
        # 无月份：规则直接挂在 year 上（如 every year on Monday / on the 15th）
        extras = []
        if self.byweekday:
            extras.append(self._weekday_text())
        if self.bymonthday:
            extras.append(self._bymonthday_text())
        return f'{base}（{"、".join(extras)}）' if extras else base


class RecurrenceParser:
    """受控自然语言循环规则解析器"""

    @staticmethod
    def parse(text: str) -> RecurrenceSpec:
        if text is None:
            raise RecurrenceParseError('无法识别的循环规则: None')

        normalized = text.strip().lower()
        if not normalized:
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')

        # 处理 “when done” 后缀（仅影响 basis）
        basis = 'scheduled'
        if normalized.endswith('when done'):
            basis = 'completed'
            normalized = normalized[: -len('when done')].strip()

        if not normalized.startswith('every'):
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')

        rest = normalized[len('every'):].strip()
        if not rest:
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')

        # 特例：every weekday
        if rest == 'weekday':
            return RecurrenceSpec(
                freq='WEEKLY',
                byweekday=['MO', 'TU', 'WE', 'TH', 'FR'],
                basis=basis,
            )

        # 家族一：单位驱动（day/week/month/year）
        m = re.match(
            r'^(?:(\d+)\s+)?(day|days|week|weeks|month|months|year|years)\b\s*(.*)$',
            rest,
        )
        if m:
            num, unit, tail = m.groups()
            spec = RecurrenceSpec(
                freq=_UNIT_TO_FREQ[unit],
                interval=int(num) if num else 1,
                basis=basis,
            )
            if tail:
                RecurrenceParser._apply_on_clause(spec, tail, text)
            return spec

        # 家族二：月份名驱动（隐式每年）
        if RecurrenceParser._month_head(rest):
            months_part, _, clause = rest.partition(' on ')
            spec = RecurrenceSpec(
                freq='YEARLY',
                bymonth=RecurrenceParser._parse_month_list(months_part, text),
                basis=basis,
            )
            if clause:
                RecurrenceParser._apply_on_clause(spec, 'on ' + clause, text)
            return spec

        raise RecurrenceParseError(f'无法识别的循环规则: {text}')

    # ---- 内部辅助 ----

    @staticmethod
    def _month_head(rest: str) -> bool:
        """判断文本开头是否为月份名"""
        first = re.split(r'[\s,]+', rest, maxsplit=1)[0]
        return first in _MONTH_NAMES

    @staticmethod
    def _parse_month_list(months_part: str, text: str) -> list[int]:
        """解析 “April and December” 之类的月份列表"""
        months = []
        for tok in (p.strip() for p in re.split(r'\s*(?:,|and)\s*', months_part)):
            value = _MONTH_NAMES.get(tok)
            if value is None:
                raise RecurrenceParseError(f'无法识别的循环规则: {text}')
            months.append(value)
        if not months:
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')
        return months

    @staticmethod
    def _apply_on_clause(spec: RecurrenceSpec, tail: str, text: str) -> None:
        """解析 “on ...” 子句，并把结果写入 spec"""
        if not tail.startswith('on'):
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')
        clause = tail[len('on'):].strip()
        if clause.startswith('the '):
            clause = clause[4:].strip()
        if not clause:
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')

        # 带位置 + 星期：last friday / 2nd last friday / 2nd wednesday
        m = re.match(r'^(\d+)(?:st|nd|rd|th)\s+last\s+(\w+)$', clause)
        if m:
            wd = _WEEKDAY_NAMES.get(m.group(2))
            if wd is None:
                raise RecurrenceParseError(f'无法识别的循环规则: {text}')
            spec.byweekday = [wd]
            spec.bysetpos = [-int(m.group(1))]
            return

        m = re.match(r'^last\s+(\w+)$', clause)
        if m:
            wd = _WEEKDAY_NAMES.get(m.group(1))
            if wd is None:
                raise RecurrenceParseError(f'无法识别的循环规则: {text}')
            spec.byweekday = [wd]
            spec.bysetpos = [-1]
            return

        m = re.match(r'^(\d+)(?:st|nd|rd|th)\s+(\w+)$', clause)
        if m:
            wd = _WEEKDAY_NAMES.get(m.group(2))
            if wd is not None:
                spec.byweekday = [wd]
                spec.bysetpos = [int(m.group(1))]
                return

        # 列表：星期列表 或 序数列表
        tokens = [p.strip() for p in re.split(r'\s*(?:,|and)\s*', clause) if p.strip()]
        weekdays, ordinals = [], []
        for tok in tokens:
            wd = _WEEKDAY_NAMES.get(tok)
            if wd is not None:
                weekdays.append(wd)
                continue
            if tok == 'last':
                ordinals.append(-1)
                continue
            mo = _ORDINAL_RE.match(tok)
            if mo:
                ordinals.append(int(mo.group(1)))
                continue
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')

        if weekdays and ordinals:
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')
        if weekdays:
            spec.byweekday = weekdays
        elif ordinals:
            spec.bymonthday = ordinals
        else:
            raise RecurrenceParseError(f'无法识别的循环规则: {text}')


class RecurrenceCalculator:
    """基于 RecurrenceSpec 的日期计算器"""

    @staticmethod
    def _to_datetime(d: date) -> datetime:
        """date → 当日零点的 datetime（dateutil 需要 datetime）"""
        return datetime(d.year, d.month, d.day)

    @staticmethod
    def _build_rrule(spec: RecurrenceSpec, dtstart: date) -> rrule:
        return rrule(dtstart=RecurrenceCalculator._to_datetime(dtstart),
                     **spec.to_rrule_kwargs())

    @staticmethod
    def _uses_implicit_monthday(spec: RecurrenceSpec) -> bool:
        """是否为未显式指定月内日期的「每 N 个月」规则

        这种规则没有 BYMONTHDAY / BYDAY，dateutil 会用 dtstart 的「日」作为隐式锚点，
        直接跳过不存在该日的月份（1 月 31 日的下一次会变成 3 月 31 日、跨过 2 月），
        与「每 N 个月」的业务语义不符，需改为按月末截断计算。
        """
        return (
            spec.freq == 'MONTHLY'
            and spec.interval >= 1
            and not (spec.byweekday or spec.bymonthday or spec.bysetpos or spec.bymonth)
        )

    @staticmethod
    def _shift_months(dtstart: date, months: int) -> date:
        """从 dtstart 按自然月平移，目标月没有该日时截断到月末

        如 2026-01-31 + 1 个月 = 2026-02-28，+ 2 个月 = 2026-03-31。
        """
        return dtstart + relativedelta(months=months)

    @staticmethod
    def _implicit_monthly_steps(spec: RecurrenceSpec, dtstart: date, after: date) -> int:
        """首个严格晚于 after 的发生日对应的「月步数」（dtstart 自身为 0 步）"""
        months = 0
        cand = dtstart
        while cand <= after:
            months += spec.interval
            cand = RecurrenceCalculator._shift_months(dtstart, months)
        return months

    @staticmethod
    def next_occurrence(spec: RecurrenceSpec, dtstart: date, after: date) -> date | None:
        """返回严格晚于 after 的下一次发生日期；规则不可满足时返回 None"""
        if RecurrenceCalculator._uses_implicit_monthday(spec):
            steps = RecurrenceCalculator._implicit_monthly_steps(spec, dtstart, after)
            return RecurrenceCalculator._shift_months(dtstart, steps)
        rule = RecurrenceCalculator._build_rrule(spec, dtstart)
        nxt = rule.after(RecurrenceCalculator._to_datetime(after), inc=False)
        return nxt.date() if nxt is not None else None

    @staticmethod
    def first_on_or_after(spec: RecurrenceSpec, seed: date) -> date | None:
        """返回首个不早于 seed 的发生日期；规则不可满足时返回 None"""
        if RecurrenceCalculator._uses_implicit_monthday(spec):
            # 「每 N 个月」以 seed 为锚点必然可满足，首个发生日就是 seed
            return seed
        rule = RecurrenceCalculator._build_rrule(spec, seed)
        nxt = rule.after(
            RecurrenceCalculator._to_datetime(seed - timedelta(days=1)), inc=False
        )
        return nxt.date() if nxt is not None else None

    @staticmethod
    def next_occurrences(spec: RecurrenceSpec, dtstart: date, after: date,
                         count: int) -> list[date]:
        """返回严格晚于 after 的前 count 次发生日期（不足则返回更少）"""
        if RecurrenceCalculator._uses_implicit_monthday(spec):
            steps = RecurrenceCalculator._implicit_monthly_steps(spec, dtstart, after)
            return [
                RecurrenceCalculator._shift_months(
                    dtstart, steps + i * spec.interval
                )
                for i in range(count)
            ]
        rule = RecurrenceCalculator._build_rrule(spec, dtstart)
        occurrences = rule.xafter(
            RecurrenceCalculator._to_datetime(after), count=count, inc=False
        )
        return [occ.date() for occ in occurrences]
