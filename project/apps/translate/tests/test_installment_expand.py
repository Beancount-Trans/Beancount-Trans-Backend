"""分期账单展开测试。"""
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory

from project.apps.translate.services.parse.installment_expander import (
    collect_refund_parent_uuids,
    expand_parsed_entry,
    parse_installment_count,
    resolve_reparse_entry,
    split_installment_amounts,
)
from project.apps.translate.services.steps import ParseStep
from project.apps.translate.utils import BILL_ALI, FormatConfig, FormatData
from project.apps.translate.views.views import ReparseEntryView


def _config():
    config = FormatConfig()
    config.ai_model = "None"
    config.deepseek_apikey = None
    return config


def _alipay_row(**overrides):
    row = {
        "transaction_time": "2024-03-01 21:08:08",
        "transaction_category": "家居家装",
        "counterparty": "公牛旗舰店",
        "commodity": "轨道插座",
        "transaction_type": "支出",
        "amount": 444.00,
        "payment_method": "中信银行信用卡分期(6428) 3期",
        "transaction_status": "交易成功",
        "notes": "/",
        "bill_identifier": BILL_ALI,
        "uuid": "2024030122001174561405075488",
        "discount": False,
    }
    row.update(overrides)
    return row


def _parsed_from_row(row, *_args, **_kwargs):
    return {
        "date": row["transaction_time"].split(" ")[0],
        "time": row["transaction_time"].split(" ")[1],
        "uuid": row["uuid"],
        "status": f"ALiPay - {row['transaction_status']}",
        "payee": row["counterparty"],
        "note": row["commodity"],
        "tag": "#Project/Decoration",
        "links": [],
        "balance": None,
        "balance_date": row["transaction_time"].split(" ")[0],
        "expense": "Expenses:Shopping:Digital",
        "expenditure_sign": "",
        "account": "Liabilities:CreditCard:Bank:CITIC:C6428",
        "account_sign": "-",
        "amount": f"{float(row['amount']):.2f}",
        "discount": row.get("discount", False),
        "currency": "CNY",
        "selected_expense_key": "公牛",
        "expense_candidates_with_score": [{"key": "公牛", "score": 0.99}],
        "_original_row": row,
        "tag_details": [],
    }


class TestInstallmentExpanderHelpers:
    def test_parse_installment_count(self):
        assert parse_installment_count("中信银行信用卡分期(6428) 3期") == 3
        assert parse_installment_count("中信银行信用卡(6428)") is None
        assert parse_installment_count("中信银行信用卡分期(6428) 1期") is None

    def test_split_installment_amounts_last_absorbs_remainder(self):
        amounts = split_installment_amounts(__import__("decimal").Decimal("5498.90"), 3)
        assert [str(amount) for amount in amounts] == ["1832.97", "1832.97", "1832.96"]


class TestExpandParsedEntry:
    def test_expand_success_row_to_purchase_plus_n_entries(self):
        row = _alipay_row()
        parsed = _parsed_from_row(row)
        expanded = expand_parsed_entry(parsed, set())

        assert len(expanded) == 4

        purchase = expanded[0]
        assert purchase["installment_role"] == "purchase"
        assert purchase["installment_period"] == 0
        assert purchase["expense"] == "Expenses:Shopping:Digital"
        assert purchase["account"] == "Liabilities:Payables"
        assert purchase["amount"] == "444.00"

        for index, entry in enumerate(expanded[1:]):
            assert entry["installment_role"] == "installment"
            assert entry["installment_period"] == index
            assert entry["expense"] == "Liabilities:Payables"
            assert entry["account"] == "Liabilities:CreditCard:Bank:CITIC:C6428"
            assert entry["amount"] == "148.00"
            assert entry["selected_expense_key"] is None
            assert entry["expense_candidates_with_score"] == []

        assert [entry["date"] for entry in expanded] == [
            "2024-03-01",
            "2024-03-01",
            "2024-04-01",
            "2024-05-01",
        ]

    def test_alipay_12_period_row_matches_ledger_case(self):
        row = _alipay_row(
            transaction_time="2026-01-20 20:21:38",
            counterparty="苏宁**店",
            amount=3399.00,
            payment_method="中信银行信用卡分期(6428) 12期&国家补贴",
            discount=True,
        )
        entries = expand_parsed_entry(_parsed_from_row(row), set())

        assert len(entries) == 13
        assert entries[0]["discount"] is True
        assert all(entry["discount"] is False for entry in entries[1:])
        assert [entry["date"] for entry in entries[1:]] == [
            f"2026-{month:02d}-20" for month in range(1, 13)
        ]
        assert [entry["amount"] for entry in entries[1:]] == ["283.25"] * 12

        formatted = [
            FormatData.format_instance(entry, config=FormatConfig())
            for entry in entries
        ]
        assert "Income:Other" in formatted[0]
        for text in formatted[1:]:
            assert "Liabilities:Payables 283.25 CNY" in text
            assert "Liabilities:CreditCard:Bank:CITIC:C6428 -283.25 CNY" in text
            assert "Income:Other" not in text

    def test_refund_row_does_not_expand(self):
        row = _alipay_row(
            transaction_status="退款成功",
            uuid="2024030122001174561405075488_123",
            payment_method="中信银行信用卡分期(6428)",
        )
        parsed = _parsed_from_row(row)
        expanded = expand_parsed_entry(parsed, set())
        assert len(expanded) == 1

    def test_parent_with_same_batch_refund_does_not_expand(self):
        payment = _alipay_row(uuid="PARENT")
        refund = _alipay_row(
            transaction_status="退款成功",
            payment_method="中信银行信用卡分期(6428)",
            uuid="PARENT_123",
        )
        parents = collect_refund_parent_uuids([payment, refund])
        expanded = expand_parsed_entry(_parsed_from_row(payment), parents)
        assert len(expanded) == 1


class TestInstallmentParseStep:
    def _execute(self, rows):
        context = {
            "owner_id": 1,
            "user": MagicMock(),
            "config": MagicMock(),
            "prefilter_bill": rows,
        }
        return ParseStep().execute(context)

    @patch("project.apps.translate.services.steps.build_ledger_index_for_user", return_value={})
    @patch(
        "project.apps.translate.services.steps.single_parse_transaction",
        side_effect=_parsed_from_row,
    )
    def test_parse_step_expands_and_assigns_cache_keys_and_links(self, _mock_parse, _mock_ledger):
        result = self._execute([_alipay_row()])
        parsed = result["parsed_data"]

        assert len(parsed) == 4
        assert [entry["cache_key"] for entry in parsed] == [
            "2024030122001174561405075488",
            "2024030122001174561405075488--2",
            "2024030122001174561405075488--3",
            "2024030122001174561405075488--4",
        ]
        assert all(entry.get("links") == ["2024030122001174561405075488"] for entry in parsed)

    @patch("project.apps.translate.services.steps.build_ledger_index_for_user", return_value={})
    @patch(
        "project.apps.translate.services.steps.single_parse_transaction",
        side_effect=_parsed_from_row,
    )
    def test_parse_step_same_batch_refund_keeps_parent_single(self, _mock_parse, _mock_ledger):
        rows = [
            _alipay_row(uuid="PARENT", amount=5498.90),
            _alipay_row(
                transaction_time="2024-03-05 15:14:31",
                uuid="PARENT_2068518355230476065",
                amount=5498.90,
                transaction_status="退款成功",
                payment_method="中信银行信用卡分期(6428)",
                commodity="退款-轨道插座",
            ),
        ]
        result = self._execute(rows)
        parsed = result["parsed_data"]
        assert len(parsed) == 2


class TestInstallmentFormatting:
    def test_format_instance_matches_expected_structure(self):
        entries = expand_parsed_entry(_parsed_from_row(_alipay_row()), set())
        formatted = [FormatData.format_instance(entry, config=FormatConfig()) for entry in entries]
        assert 'Expenses:Shopping:Digital 444.00 CNY' in formatted[0]
        assert 'Liabilities:Payables -444.00 CNY' in formatted[0]
        assert 'Liabilities:Payables 148.00 CNY' in formatted[1]
        assert 'Liabilities:CreditCard:Bank:CITIC:C6428 -148.00 CNY' in formatted[1]


@pytest.mark.django_db
class TestInstallmentReparseSlice:
    @patch("project.apps.translate.views.views.get_user_config", return_value=_config())
    @patch("project.apps.translate.views.views.get_token_user_id", return_value=1)
    @patch("project.apps.translate.views.views.User.objects.get")
    @patch(
        "project.apps.translate.views.views.single_parse_transaction",
        side_effect=_parsed_from_row,
    )
    @patch("project.apps.translate.views.views.resolve_refund_peer_for_row", return_value=None)
    def test_reparse_updates_purchase_slice_only(
        self,
        _mock_refund_peer,
        _mock_parse,
        mock_user_get,
        _mock_user_id,
        _mock_config,
    ):
        mock_user_get.return_value = MagicMock(id=1)
        row = _alipay_row()
        purchase = resolve_reparse_entry(
            _parsed_from_row(row),
            row,
            installment_role="purchase",
            installment_period=0,
        )
        cache.set(
            "ORDER1",
            {
                "original_row": row,
                "parsed_entry": purchase,
            },
            timeout=3600,
        )

        factory = APIRequestFactory()
        request = factory.post("/translate/reparse", {"entry_id": "ORDER1", "selected_key": "装修"})
        response = ReparseEntryView.as_view()(request)

        assert response.status_code == 200
        assert response.data["ai_choose"] == "装修"
        assert "Expenses:Shopping:Digital 444.00 CNY" in response.data["formatted"]
        assert "Liabilities:Payables -444.00 CNY" in response.data["formatted"]

    def test_resolve_reparse_entry_for_installment_keeps_payables_structure(self):
        row = _alipay_row()
        parsed = _parsed_from_row(row)
        installment = resolve_reparse_entry(
            parsed,
            row,
            installment_role="installment",
            installment_period=2,
        )
        assert installment["expense"] == "Liabilities:Payables"
        assert installment["account"] == "Liabilities:CreditCard:Bank:CITIC:C6428"
        assert installment["amount"] == "148.00"
