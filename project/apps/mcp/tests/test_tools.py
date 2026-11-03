"""MCP Tools 的注册信息与调用行为测试。"""
import asyncio
from datetime import date
from unittest.mock import patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from .conftest import SAMPLE_BEAN

pytestmark = pytest.mark.django_db(transaction=True)

EXPECTED_TOOLS = {'get_ledger_context', 'run_bql', 'get_journal', 'read_ledger_file'}


def _call(server, name, arguments):
    return asyncio.run(server.call_tool(name, arguments))


@pytest.fixture
def tools(mcp_server):
    return {tool.name: tool for tool in asyncio.run(mcp_server.list_tools())}


class TestToolRegistration:
    def test_registers_expected_tools(self, tools):
        assert set(tools) == EXPECTED_TOOLS

    def test_every_tool_has_description_and_schema(self, tools):
        for tool in tools.values():
            assert tool.description
            assert tool.input_schema['type'] == 'object'

    def test_tool_arguments(self, tools):
        assert 'query' in tools['run_bql'].input_schema['properties']
        assert 'path' in tools['read_ledger_file'].input_schema['properties']
        assert tools['get_ledger_context'].input_schema['properties'] == {}
        journal_properties = tools['get_journal'].input_schema['properties']
        assert set(journal_properties) == {'start', 'end', 'limit', 'offset'}


class TestGetLedgerContext:
    def test_returns_context_text(self, mcp_server, ledger_files):
        result = asyncio.run(mcp_server.call_tool('get_ledger_context', {}))
        assert result.is_error is not True
        text = result.content[0].text
        assert '基准日期' in text
        assert 'Expenses:Food' in text

    def test_includes_platform_catalog(self, mcp_server, ledger_files, platform_metadata):
        result = asyncio.run(mcp_server.call_tool('get_ledger_context', {}))
        assert '餐饮' in result.content[0].text

    def test_records_usage_after_success(self, mcp_server, ledger_files):
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch:
            asyncio.run(mcp_server.call_tool('get_ledger_context', {}))

        touch.assert_called_once_with()


class TestRunBql:
    def test_returns_structured_result(self, mcp_server, ledger_files):
        result = asyncio.run(
            mcp_server.call_tool(
                'run_bql',
                {'query': "SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account"},
            )
        )
        payload = result.structured_content
        assert payload['row_count'] >= 1
        assert payload['truncated'] is False
        assert 'Expenses:Food' in payload['result_text']
        assert payload['bql'].startswith('SELECT')

    def test_enriches_with_platform_description(self, mcp_server, ledger_files, platform_metadata):
        result = asyncio.run(
            mcp_server.call_tool(
                'run_bql',
                {'query': "SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account"},
            )
        )
        assert '餐饮（Expenses:Food）' in result.structured_content['result_text']

    def test_records_usage_after_success(self, mcp_server, ledger_files):
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch:
            asyncio.run(
                mcp_server.call_tool(
                    'run_bql',
                    {'query': "SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account"},
                )
            )

        touch.assert_called_once_with()

    def test_does_not_record_usage_when_validation_fails(self, mcp_server, ledger_files):
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch:
            with pytest.raises(ToolError, match='BQL 校验失败'):
                _call(mcp_server, 'run_bql', {'query': 'INSERT INTO foo VALUES (1)'})

        touch.assert_not_called()

    def test_does_not_record_usage_when_ledger_missing(
        self, mcp_server, settings, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', tmp_path / 'nonexistent')
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch:
            with pytest.raises(ToolError, match='账本不可用'):
                _call(mcp_server, 'run_bql', {'query': 'SELECT account'})

        touch.assert_not_called()

    def test_rejects_write_statement(self, mcp_server, ledger_files):
        with pytest.raises(ToolError, match='BQL 校验失败'):
            _call(mcp_server, 'run_bql', {'query': 'INSERT INTO foo VALUES (1)'})

    def test_rejects_units_position_comparison(self, mcp_server, ledger_files):
        with pytest.raises(ToolError, match='BQL 校验失败'):
            _call(
                mcp_server,
                'run_bql',
                {'query': "SELECT date WHERE account ~ 'Expenses' AND units(position) > 100"},
            )

    def test_missing_ledger_reports_tool_error(self, mcp_server, settings, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', tmp_path / 'nonexistent')
        with pytest.raises(ToolError, match='账本不可用'):
            _call(mcp_server, 'run_bql', {'query': 'SELECT account'})

    def test_isolated_between_users(self, mcp_server, ledger_files, other_user, other_assets, settings, monkeypatch):
        monkeypatch.setattr(settings, 'MCP_DEV_USERNAME', other_user.username)
        result = asyncio.run(
            mcp_server.call_tool(
                'run_bql',
                {'query': "SELECT account, sum(position) WHERE account ~ 'Expenses' GROUP BY account"},
            )
        )
        assert 'Expenses:Rent' in result.structured_content['result_text']
        assert 'Expenses:Food' not in result.structured_content['result_text']


class TestGetJournal:
    def _journal(self, server, **arguments):
        return asyncio.run(server.call_tool('get_journal', arguments)).structured_content

    def test_returns_entries_newest_first(self, mcp_server, ledger_files):
        payload = self._journal(mcp_server, start='2024-01-01', end='2024-01-31')
        assert payload['start'] == '2024-01-01'
        assert payload['end'] == '2024-01-31'
        assert payload['total'] == 2
        assert [entry['date'] for entry in payload['entries']] == ['2024-01-10', '2024-01-05']
        assert payload['offset'] == 0
        assert payload['has_more'] is False
        assert payload['next_offset'] is None

    def test_entry_shape_and_descriptions(self, mcp_server, ledger_files, platform_metadata):
        payload = self._journal(mcp_server, start='2024-01-01', end='2024-01-31')
        lunch = payload['entries'][1]
        assert lunch['id']
        assert lunch['payee'] == '午餐'
        assert lunch['narration'] == '餐厅'
        assert lunch['tags'] == []
        assert [
            (posting['account'], posting['number'], posting['currency'])
            for posting in lunch['postings']
        ] == [
            ('Expenses:Food', '50.00', 'CNY'),
            ('Assets:Cash', '-50.00', 'CNY'),
        ]
        assert [posting['description'] for posting in lunch['postings']] == ['餐饮', '现金']

    def test_paginates_by_transaction(self, mcp_server, ledger_files):
        first = self._journal(mcp_server, start='2024-01-01', end='2024-01-31', limit=1)
        assert first['total'] == 2
        assert [entry['date'] for entry in first['entries']] == ['2024-01-10']
        assert first['has_more'] is True
        assert first['next_offset'] == 1

        second = self._journal(
            mcp_server, start='2024-01-01', end='2024-01-31', limit=1, offset=1,
        )
        assert [entry['date'] for entry in second['entries']] == ['2024-01-05']
        assert second['has_more'] is False
        assert second['next_offset'] is None

    def test_filters_by_date_range(self, mcp_server, ledger_files):
        payload = self._journal(mcp_server, start='2024-01-06', end='2024-01-31')
        assert [entry['narration'] for entry in payload['entries']] == ['工资']

    def test_returns_tags(self, mcp_server, ledger_files):
        ledger_files['main'].write_text(
            '2024-01-05 * "餐厅" "午餐" #Discretionary\n'
            '  Expenses:Food  50.00 CNY\n'
            '  Assets:Cash  -50.00 CNY\n',
            encoding='utf-8',
        )
        payload = self._journal(mcp_server, start='2024-01-05', end='2024-01-05')
        assert payload['entries'][0]['tags'] == ['Discretionary']

    def test_defaults_to_last_30_days(self, mcp_server, ledger_files):
        with patch(
            'project.apps.assistant.services.reference_date.get_reference_date',
            return_value=date(2024, 1, 31),
        ):
            payload = self._journal(mcp_server)
        assert payload['start'] == '2024-01-02'
        assert payload['end'] == '2024-01-31'

    def test_isolated_between_users(
        self, mcp_server, other_assets, other_user, settings, monkeypatch,
    ):
        monkeypatch.setattr(settings, 'MCP_DEV_USERNAME', other_user.username)
        payload = self._journal(mcp_server, start='2024-03-01', end='2024-03-31')
        assert payload['entries'][0]['narration'] == '房租'
        assert payload['entries'][0]['postings'][0]['account'] == 'Expenses:Rent'

    def test_records_usage_after_success(self, mcp_server, ledger_files):
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch, patch(
            'project.apps.assistant.services.journal_query.record_bql'
        ) as record:
            self._journal(mcp_server, start='2024-01-01', end='2024-01-31')

        touch.assert_called_once_with()
        record.assert_called_once()
        assert record.call_args.kwargs['source_type'] == 'journal_read'
        assert record.call_args.kwargs['row_count'] == 4

    @pytest.mark.parametrize(
        'arguments, match',
        [
            ({'start': '2024/01/01', 'end': '2024-01-31'}, 'YYYY-MM-DD'),
            ({'start': '2024-02-01', 'end': '2024-01-01'}, 'start 不能晚于 end'),
            ({'start': '2023-01-01', 'end': '2024-12-31'}, '366'),
            ({'start': '2024-01-01', 'end': '2024-01-31', 'limit': 0}, 'limit'),
            ({'start': '2024-01-01', 'end': '2024-01-31', 'limit': 501}, 'limit'),
            ({'start': '2024-01-01', 'end': '2024-01-31', 'offset': -1}, 'offset'),
        ],
    )
    def test_rejects_invalid_arguments(
        self, mcp_server, ledger_files, arguments, match,
    ):
        with pytest.raises(ToolError, match=match):
            _call(mcp_server, 'get_journal', arguments)

    def test_missing_ledger_reports_tool_error(
        self, mcp_server, settings, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', tmp_path / 'nonexistent')
        with pytest.raises(ToolError, match='账本不可用'):
            _call(mcp_server, 'get_journal', {'start': '2024-01-01', 'end': '2024-01-31'})


class TestReadLedgerFile:
    def test_reads_main_bean(self, mcp_server, ledger_files):
        result = asyncio.run(mcp_server.call_tool('read_ledger_file', {'path': 'main.bean'}))
        payload = result.structured_content
        assert payload['path'] == 'main.bean'
        assert payload['content'] == SAMPLE_BEAN
        assert payload['size_bytes'] == len(SAMPLE_BEAN.encode('utf-8'))

    def test_records_usage_after_success(self, mcp_server, ledger_files):
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch:
            asyncio.run(mcp_server.call_tool('read_ledger_file', {'path': 'main.bean'}))

        touch.assert_called_once_with()

    def test_does_not_record_usage_when_path_invalid(self, mcp_server, ledger_files):
        with patch(
            'project.apps.mcp.tools.touch_current_personal_access_token'
        ) as touch:
            with pytest.raises(ToolError, match='只允许读取自己账本目录内的文件'):
                _call(mcp_server, 'read_ledger_file', {'path': '../outside.bean'})

        touch.assert_not_called()

    def test_blocks_path_traversal(self, mcp_server, ledger_files):
        with pytest.raises(ToolError, match='只允许读取自己账本目录内的文件'):
            _call(mcp_server, 'read_ledger_file', {'path': '../outside.bean'})

    def test_rejects_non_bean_file(self, mcp_server, ledger_files):
        with pytest.raises(ToolError, match='后缀'):
            _call(mcp_server, 'read_ledger_file', {'path': 'notes.txt'})

    def test_rejects_missing_file(self, mcp_server, ledger_files):
        with pytest.raises(ToolError, match='文件不存在'):
            _call(mcp_server, 'read_ledger_file', {'path': 'nope.bean'})


class TestErrors:
    def test_unknown_tool(self, mcp_server):
        with pytest.raises(ToolError):
            _call(mcp_server, 'no_such_tool', {})

    def test_missing_identity(self, mcp_server, settings, monkeypatch):
        monkeypatch.setattr(settings, 'MCP_DEV_USERNAME', '')
        with pytest.raises(ToolError, match='未提供访问令牌'):
            _call(mcp_server, 'get_ledger_context', {})
