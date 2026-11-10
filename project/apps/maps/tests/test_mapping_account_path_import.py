"""映射通过账户路径字符串创建 / 批量导入时的校验。"""
import pytest
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APIClient

from project.apps.account.models import Account
from project.apps.maps.models import Assets, Expense, Income


@pytest.mark.django_db
class TestMappingAccountPathImport:
    def test_expense_create_with_expend_account_only(self):
        user = User.objects.create_user("map_u1", "map_u1@t.com", "pw")
        Account.objects.create(account="Expenses:Food", owner=user)
        client = APIClient()
        client.force_authenticate(user=user)
        r = client.post(
            "/api/expense/",
            {
                "key": "e_key",
                "payee": "shop",
                "expend_account": "Expenses:Food",
                "currency": "CNY",
            },
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        exp = Expense.objects.get(key="e_key", owner=user)
        assert exp.expend is not None
        assert exp.expend.account == "Expenses:Food"

    def test_expense_batch_unknown_account_path_sets_null(self):
        user = User.objects.create_user("map_u2", "map_u2@t.com", "pw")
        client = APIClient()
        client.force_authenticate(user=user)
        r = client.post(
            "/api/expense/",
            [{"key": "x", "expend_account": "Expenses:DoesNotExist"}],
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        exp = Expense.objects.get(key="x", owner=user)
        assert exp.expend is None

    def test_assets_create_with_assets_account_only(self):
        user = User.objects.create_user("map_u3", "map_u3@t.com", "pw")
        Account.objects.create(account="Assets:Cash", owner=user)
        client = APIClient()
        client.force_authenticate(user=user)
        r = client.post(
            "/api/assets/",
            {
                "key": "a_key",
                "assets_account": "Assets:Cash",
            },
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        row = Assets.objects.get(key="a_key", owner=user)
        assert row.assets is not None
        assert row.assets.account == "Assets:Cash"

    def test_assets_batch_unknown_account_path_sets_null(self):
        user = User.objects.create_user("map_u4", "map_u4@t.com", "pw")
        client = APIClient()
        client.force_authenticate(user=user)
        r = client.post(
            "/api/assets/",
            [{"key": "y", "assets_account": "Assets:Nope"}],
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        row = Assets.objects.get(key="y", owner=user)
        assert row.assets is None

    def test_income_create_with_income_account_only(self):
        user = User.objects.create_user("map_u5", "map_u5@t.com", "pw")
        Account.objects.create(account="Income:Salary", owner=user)
        client = APIClient()
        client.force_authenticate(user=user)
        r = client.post(
            "/api/income/",
            {
                "key": "i_key",
                "income_account": "Income:Salary",
            },
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        row = Income.objects.get(key="i_key", owner=user)
        assert row.income is not None
        assert row.income.account == "Income:Salary"

    def test_income_batch_unknown_account_path_sets_null(self):
        user = User.objects.create_user("map_u6", "map_u6@t.com", "pw")
        client = APIClient()
        client.force_authenticate(user=user)
        r = client.post(
            "/api/income/",
            [{"key": "z", "income_account": "Income:Nope"}],
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        row = Income.objects.get(key="z", owner=user)
        assert row.income is None
