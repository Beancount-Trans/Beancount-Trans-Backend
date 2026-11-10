import re

from project.apps.external_accounts.models import Institution, InstitutionAlias

_TRANSLATE = str.maketrans({'（': '(', '）': ')', '　': ' '})


def normalize_text(text: str) -> str:
    """归一化账单文本：去掉空白，统一全角括号。"""
    return re.sub(r'\s+', '', (text or '').translate(_TRANSLATE))


def match_institution(text: str):
    """把账单里的机构写法归一到一个机构。

    优先取最长的命中别名，避免「中信」压过「中信银行」这类包含关系。
    """
    normalized = normalize_text(text)
    if not normalized:
        return None

    best_length = 0
    best_institution = None
    for alias in InstitutionAlias.objects.select_related('institution').filter(
        institution__enable=True
    ):
        key = normalize_text(alias.text)
        if not key or key not in normalized:
            continue
        if len(key) > best_length:
            best_length = len(key)
            best_institution = alias.institution
    return best_institution


def institution_names() -> dict:
    """机构编码到规范名的映射，供适配器与展示复用。"""
    return dict(Institution.objects.filter(enable=True).values_list('code', 'name'))
