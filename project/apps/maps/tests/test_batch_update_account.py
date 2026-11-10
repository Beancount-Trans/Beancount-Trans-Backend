"""映射批量更新账户 API 测试。"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from project.apps.account.models import Account
from project.apps.maps.models import Assets, Expense, Income

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username='mapbatchuser',
        email='mapbatch@example.com',
        password='testpass123',
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        username='mapbatchother',
        email='mapbatchother@example.com',
        password='testpass123',
    )


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def expense_account(user):
    return Account.objects.create(account='Expenses:Food', owner=user)


@pytest.fixture
def income_account(user):
    return Account.objects.create(account='Income:Salary', owner=user)


@pytest.fixture
def assets_account(user):
    return Account.objects.create(account='Assets:Bank:CMB', owner=user)


@pytest.fixture
def target_expense_account(user):
    return Account.objects.create(account='Expenses:Dining', owner=user)


@pytest.fixture
def target_income_account(user):
    return Account.objects.create(account='Income:Bonus', owner=user)


@pytest.fixture
def target_assets_account(user):
    return Account.objects.create(account='Assets:Bank:ICBC', owner=user)


@pytest.mark.django_db
class TestExpenseBatchUpdateAccount:
    def test_success(self, api_client, user, expense_account, target_expense_account):
        e1 = Expense.objects.create(owner=user, key='美团', expend=expense_account, currency='CNY')
        e2 = Expense.objects.create(owner=user, key='饿了么', expend=expense_account, payee='饿了么')

        url = reverse('expense-batch-update-account')
        response = api_client.post(url, {
            'expense_ids': [e1.id, e2.id],
            'expend_id': target_expense_account.id,
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['updated_count'] == 2

        e1.refresh_from_db()
        e2.refresh_from_db()
        assert e1.expend_id == target_expense_account.id
        assert e2.expend_id == target_expense_account.id
        assert e1.currency == 'CNY'
        assert e2.payee == '饿了么'

    def test_missing_expend_id(self, api_client, user, expense_account):
        expense = Expense.objects.create(owner=user, key='测试', expend=expense_account)
        url = reverse('expense-batch-update-account')
        response = api_client.post(url, {'expense_ids': [expense.id]}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_foreign_mapping_id(self, api_client, user, other_user, expense_account, target_expense_account):
        other_expense = Expense.objects.create(owner=other_user, key='他人', expend=expense_account)
        url = reverse('expense-batch-update-account')
        response = api_client.post(url, {
            'expense_ids': [other_expense.id],
            'expend_id': target_expense_account.id,
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'error' in response.data

    def test_invalid_account(self, api_client, user, expense_account):
        expense = Expense.objects.create(owner=user, key='测试', expend=expense_account)
        url = reverse('expense-batch-update-account')
        response = api_client.post(url, {
            'expense_ids': [expense.id],
            'expend_id': 99999,
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_currency_field(self, api_client, user, expense_account, target_expense_account):
        expense = Expense.objects.create(owner=user, key='测试', expend=expense_account, currency='USD')
        url = reverse('expense-batch-update-account')
        response = api_client.post(url, {
            'expense_ids': [expense.id],
            'expend_id': target_expense_account.id,
            'currency': 'EUR',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        expense.refresh_from_db()
        assert expense.currency == 'USD'


@pytest.mark.django_db
class TestIncomeBatchUpdateAccount:
    def test_success(self, api_client, user, income_account, target_income_account):
        i1 = Income.objects.create(owner=user, key='红包', income=income_account)
        i2 = Income.objects.create(owner=user, key='转账', income=income_account, payer='微信')

        url = reverse('income-batch-update-account')
        response = api_client.post(url, {
            'income_ids': [i1.id, i2.id],
            'income_id': target_income_account.id,
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['updated_count'] == 2
        i1.refresh_from_db()
        i2.refresh_from_db()
        assert i1.income_id == target_income_account.id
        assert i2.payer == '微信'

    def test_missing_income_id(self, api_client, user, income_account):
        income = Income.objects.create(owner=user, key='测试', income=income_account)
        url = reverse('income-batch-update-account')
        response = api_client.post(url, {'income_ids': [income.id]}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestAssetsBatchUpdateAccount:
    def test_success(self, api_client, user, assets_account, target_assets_account):
        a1 = Assets.objects.create(owner=user, key='6222', assets=assets_account)
        a2 = Assets.objects.create(owner=user, key='6223', assets=assets_account)

        url = reverse('assets-batch-update-account')
        response = api_client.post(url, {
            'assets_ids': [a1.id, a2.id],
            'assets_id': target_assets_account.id,
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['updated_count'] == 2
        a1.refresh_from_db()
        assert a1.assets_id == target_assets_account.id

    def test_missing_assets_id(self, api_client, user, assets_account):
        asset = Assets.objects.create(owner=user, key='6222', assets=assets_account)
        url = reverse('assets-batch-update-account')
        response = api_client.post(url, {'assets_ids': [asset.id]}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
