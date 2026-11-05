"""根账户规范与清理测试"""
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command

from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem
from project.apps.account.signals import apply_official_account_templates
from project.utils.file import BeanFileManager

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


@pytest.fixture
def user(assets_dir):
    return User.objects.create_user(username='rootuser', password='testpass123')


def _read(user, relative_path):
    return (
        Path(BeanFileManager.get_user_assets_path(user)) / relative_path
    ).read_text(encoding='utf-8')


def test_root_account_is_rejected(user):
    with pytest.raises(ValidationError):
        Account.objects.create(account='Assets', owner=user)


def test_top_level_child_does_not_create_root(user):
    Account.objects.create(account='Assets:Savings', owner=user)

    assert not Account.objects.filter(owner=user, account='Assets').exists()
    assert Account.objects.get(owner=user, account='Assets:Savings').parent is None

    text = _read(user, 'trans/collect.bean')
    assert 'open Assets:Savings' in text
    assert 'open Assets\n' not in text

    loader = pytest.importorskip('beancount.loader')
    main_path = BeanFileManager.get_main_bean_path(user)
    _entries, errors, _options = loader.load_file(main_path)
    assert not any('Invalid token' in str(error) for error in errors), errors


def test_official_template_skips_root_items(user):
    template = AccountTemplate.objects.create(
        name='根账户测试模板',
        owner=user,
        is_official=True,
    )
    AccountTemplateItem.objects.create(template=template, account_path='Assets')
    AccountTemplateItem.objects.create(
        template=template, account_path='Assets:Savings'
    )

    apply_official_account_templates(user)

    assert not Account.objects.filter(owner=user, account='Assets').exists()
    assert Account.objects.filter(owner=user, account='Assets:Savings').exists()
    text = _read(user, 'trans/collect.bean')
    assert 'open Assets:Savings' in text
    assert 'open Assets\n' not in text


def test_cleanup_removes_historical_root(user):
    # 模拟历史脏数据：绕过 save 直接写入根账户
    Account.objects.bulk_create([Account(account='Assets', owner=user)])
    child = Account.objects.create(account='Assets:Savings', owner=user)
    assert child.parent.account == 'Assets'

    collect_path = Path(BeanFileManager.get_user_assets_path(user)) / 'trans/collect.bean'
    collect_path.write_text(
        '2000-01-01 open Assets\n'
        '2000-01-01 open Assets:Savings CNY\n',
        encoding='utf-8',
    )

    call_command('cleanup_root_accounts', '--user', user.username)

    assert not Account.objects.filter(owner=user, account='Assets').exists()
    child.refresh_from_db()
    assert child.parent is None
    text = collect_path.read_text(encoding='utf-8')
    assert 'open Assets\n' not in text
    assert 'open Assets:Savings CNY' in text
