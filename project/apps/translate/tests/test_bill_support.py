"""账单解析可用性记录与公开接口测试。"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from project.apps.translate.models import BillParseStat, FormatConfig
from project.apps.translate.services.analyze_service import AnalyzeService
from project.apps.translate.services.bill_support import (
    BILL_SUPPORT_TYPES,
    build_bill_support_snapshot,
    extract_latest_bill_date,
    record_parse_success,
)


def _context(**overrides):
    context = {
        'status': 'success',
        'bill_type': 'wechat',
        'initialized_bill': [
            {'transaction_time': '2026-10-01 10:00:00'},
            {'transaction_time': '2026-10-31 18:00:00'},
        ],
        'parsed_data': [{'date': '2027-01-01'}],
        'formatted_data': [{'formatted': 'demo'}],
    }
    context.update(overrides)
    return context


@pytest.mark.django_db
class TestBillSupportService:
    def test_record_parse_success_creates_stat(self):
        record_parse_success('wechat', date(2026, 10, 31))

        stat = BillParseStat.objects.get(bill_type='wechat')
        assert stat.latest_bill_date == date(2026, 10, 31)
        assert stat.last_success_at is not None

    def test_latest_bill_date_never_regresses(self):
        old_date = date(2026, 10, 31)
        record_parse_success('wechat', old_date)
        first_success = BillParseStat.objects.get(bill_type='wechat').last_success_at

        record_parse_success('wechat', date(2026, 9, 1))

        stat = BillParseStat.objects.get(bill_type='wechat')
        assert stat.latest_bill_date == old_date
        assert stat.last_success_at >= first_success

    def test_unknown_bill_type_is_ignored(self):
        record_parse_success('unknown_bill', date(2026, 10, 31))
        assert not BillParseStat.objects.exists()

    def test_extract_latest_bill_date_uses_raw_rows(self):
        latest = extract_latest_bill_date(_context())
        assert latest == date(2026, 10, 31)

    def test_extract_latest_bill_date_falls_back_to_parsed_data(self):
        context = _context(initialized_bill=[])
        assert extract_latest_bill_date(context) == date(2027, 1, 1)

    def test_snapshot_contains_all_supported_types(self):
        record_parse_success('wechat', date(2026, 10, 31))

        snapshot = build_bill_support_snapshot()

        assert [item['bill_type'] for item in snapshot] == list(BILL_SUPPORT_TYPES)
        wechat = next(item for item in snapshot if item['bill_type'] == 'wechat')
        assert wechat['latest_bill_date'] == '2026-10-31'
        assert wechat['last_success_at'] is not None
        assert all(
            item['last_success_at'] is None
            for item in snapshot
            if item['bill_type'] != 'wechat'
        )


@pytest.mark.django_db
class TestAnalyzeServiceRecordsSuccess:
    def _analyze(self, user, pipeline_result):
        service = AnalyzeService(user=user, config=FormatConfig.get_user_config(user))
        uploaded_file = SimpleUploadedFile('bill.csv', b'x', content_type='text/csv')
        with patch(
            'project.apps.translate.services.analyze_service.BillParsingPipeline.process',
            return_value=pipeline_result,
        ):
            service.analyze_single_file(uploaded_file, {'write': False})

    def test_success_records_bill_health(self, user):
        self._analyze(user, _context())

        stat = BillParseStat.objects.get(bill_type='wechat')
        assert stat.latest_bill_date == date(2026, 10, 31)

    def test_error_context_does_not_record(self, user):
        self._analyze(
            user,
            {'status': 'error', 'bill_type': 'wechat', 'errors': ['解析失败']},
        )
        assert not BillParseStat.objects.exists()

    def test_empty_formatted_data_does_not_record(self, user):
        self._analyze(user, _context(formatted_data=[]))
        assert not BillParseStat.objects.exists()


@pytest.mark.django_db
class TestBillSupportStatusView:
    def test_anonymous_can_read_support_status(self):
        record_parse_success('wechat', timezone.localdate() - timedelta(days=1))

        response = APIClient().get('/api/translate/bill-support')

        assert response.status_code == status.HTTP_200_OK
        bills = response.data['bills']
        assert len(bills) == len(BILL_SUPPORT_TYPES)
        wechat = next(item for item in bills if item['bill_type'] == 'wechat')
        assert wechat['latest_bill_date'] == (
            timezone.localdate() - timedelta(days=1)
        ).isoformat()
        alipay = next(item for item in bills if item['bill_type'] == 'alipay')
        assert alipay['last_success_at'] is None
