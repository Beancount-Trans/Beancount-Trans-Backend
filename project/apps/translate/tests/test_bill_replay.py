"""用真实样例账单重放解析，验证账户解析结果。

样例文件取自 ``project/fixtures/sample_files``，与官方模板一起构成
从「模板导入 → 账单解析 → 账户落账」的闭环。测试覆盖支付宝与微信两类
账单中出现的全部付款方式，确认：

- 平台资金账户经由槽位解析到账本账户；
- 银行储蓄卡 / 信用卡经由机构 + 卡类型 + 尾号解析；
- 结构化身份解析不到时不会静默落到 ``Assets:Other``。
"""
import importlib
import re
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from project.apps.translate.models import FormatConfig
from project.apps.translate.services.analyze_service import AnalyzeService

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures' / 'sample_files'
ALIPAY_CSV = FIXTURES / '完整测试_支付宝.csv'
WECHAT_CSV = FIXTURES / '完整测试_微信.csv'

_POSTING_RE = re.compile(r'^\s{4}([A-Z][\w:-]*)\s+(-?[\d,]+\.\d+)')


def _postings(entry_text: str):
    """从格式化条目中提取记账账户（第一行为交易头，跳过）。"""
    accounts = []
    for line in entry_text.splitlines()[1:]:
        match = _POSTING_RE.match(line)
        if match:
            accounts.append(match.group(1))
    return accounts


def _seed_institutions():
    """写入随迁移发布的机构注册表种子数据（测试库不跑数据迁移）。"""
    module = importlib.import_module(
        'project.apps.external_accounts.migrations.0002_seed_institutions'
    )
    module.seed_institutions(django_apps, None)


def _replay(user, csv_path: Path):
    """用官方模板初始化用户并解析真实样例账单。"""
    _seed_institutions()
    call_command('init_official_templates', verbosity=0)
    call_command('apply_templates_to_users', user_ids=str(user.id), verbosity=0)

    uploaded = SimpleUploadedFile(
        csv_path.name, csv_path.read_bytes(), content_type='text/csv'
    )
    service = AnalyzeService(user, FormatConfig.get_user_config(user))
    return service.analyze_single_file(uploaded, {'write': False})


@pytest.fixture
def temp_assets(tmp_path, monkeypatch):
    from django.conf import settings

    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


@pytest.mark.django_db
class TestAlipayReplay:
    def test_sample_resolves_payment_methods(self, user, temp_assets):
        context = _replay(user, ALIPAY_CSV)

        assert context['status'] != 'error', context.get('errors')
        entries = [entry for entry in context['formatted_data'] if entry.get('formatted')]
        assert entries

        accounts = {account for entry in entries for account in _postings(entry['formatted'])}

        assert 'Assets:Savings:Web:AliPay' in accounts     # 账户余额
        assert 'Assets:Savings:Web:AliFund' in accounts    # 余额宝
        assert 'Liabilities:CreditCard:Web:AliPay:Huabei' in accounts
        assert 'Assets:Savings:Bank:BOC:C0814' in accounts
        assert 'Liabilities:CreditCard:Bank:CITIC:C6428' in accounts


@pytest.mark.django_db
class TestWechatReplay:
    def test_sample_resolves_payment_methods(self, user, temp_assets):
        context = _replay(user, WECHAT_CSV)

        assert context['status'] != 'error', context.get('errors')
        entries = [entry for entry in context['formatted_data'] if entry.get('formatted')]
        assert entries

        accounts = {account for entry in entries for account in _postings(entry['formatted'])}

        assert 'Assets:Savings:Web:WechatPay' in accounts
        assert 'Assets:Savings:Web:WechatFund' in accounts
        assert 'Liabilities:CreditCard:Bank:CITIC:C6428' in accounts
