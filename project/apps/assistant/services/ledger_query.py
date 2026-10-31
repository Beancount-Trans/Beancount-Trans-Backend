"""封装 beanquery 对用户账本的只读查询。"""
import io
import logging
import os
from dataclasses import dataclass
from typing import Optional

import beanquery
from beanquery.compiler import CompilationError
from beanquery.query_render import render_text
from django.conf import settings
from django.contrib.auth.models import User

from project.apps.translate.models import FormatConfig
from project.utils.file import BeanFileManager

from .bql_errors import format_bql_error
from .bql_validator import BQLValidationError, validate_bql
from .metadata_catalog import build_path_to_description_map
from .result_enricher import enrich_bql_result_text
from .result_normalizer import normalize_zero_balance_sums

logger = logging.getLogger(__name__)


class LedgerNotFoundError(FileNotFoundError):
    """用户账本文件不存在。"""


@dataclass
class BQLQueryResult:
    bql: str
    result_text: str
    row_count: int
    truncated: bool


class LedgerQueryService:
    """对用户 main.bean 执行 BQL 查询。"""

    def __init__(self, user: User):
        self.user = user
        self.ledger_path = BeanFileManager.get_main_bean_path(user)
        self.max_rows = int(getattr(settings, 'ASSISTANT_MAX_BQL_ROWS', 100))

    def ledger_exists(self) -> bool:
        return os.path.isfile(self.ledger_path)

    def _connect(self):
        if not self.ledger_exists():
            raise LedgerNotFoundError(f'账本文件不存在: {self.ledger_path}')
        conn = beanquery.connect(None)
        conn.attach(f'beancount:{self.ledger_path}')
        return conn

    def execute(self, query: str, *, enrich: bool = True, meter: bool = True) -> BQLQueryResult:
        try:
            bql = validate_bql(query)
        except BQLValidationError as exc:
            self._record_bql(meter, ok=False, error=str(exc))
            raise

        try:
            conn = self._connect()
        except Exception as exc:
            self._record_bql(meter, ok=False, error=f'{type(exc).__name__}: {exc}')
            raise

        try:
            cursor = conn.execute(bql)
            rows = cursor.fetchall()
            row_count = len(rows)
            truncated = row_count > self.max_rows
            if truncated:
                rows = rows[:self.max_rows]

            out = io.StringIO()
            dcontext = conn.options.get('dcontext')
            render_text(cursor.description, rows, dcontext, out)
            result_text = out.getvalue()
            if truncated:
                result_text += f'\n... (结果已截断，仅显示前 {self.max_rows} 行，共 {row_count} 行)'
            currency = FormatConfig.get_user_config(self.user).currency or 'CNY'
            result_text = normalize_zero_balance_sums(result_text, bql, currency)
            if enrich and result_text:
                path_map = build_path_to_description_map(self.user)
                result_text = enrich_bql_result_text(result_text, path_map)
            self._record_bql(meter, ok=True, row_count=row_count)
            return BQLQueryResult(
                bql=bql,
                result_text=result_text or '(无结果)',
                row_count=row_count,
                truncated=truncated,
            )
        except BQLValidationError as exc:
            self._record_bql(meter, ok=False, error=str(exc))
            raise
        except CompilationError as exc:
            logger.warning('BQL 编译失败: %s — %s', bql, exc)
            self._record_bql(meter, ok=False, error=f'CompilationError: {exc}')
            raise ValueError(format_bql_error(exc)) from exc
        except Exception as exc:
            logger.exception('BQL 执行失败: %s', bql)
            self._record_bql(meter, ok=False, error=f'{type(exc).__name__}: {exc}')
            raise ValueError(format_bql_error(exc)) from exc

    def _record_bql(self, meter: bool, *, ok: bool, row_count: int = 0, error: str = '') -> None:
        """记录 BQL 用量；内部系统调用（meter=False）不计数。"""
        if not meter:
            return
        from project.apps.usage.services import record_bql

        record_bql(
            user_id=getattr(self.user, 'id', None),
            ok=ok,
            row_count=row_count,
            error=error,
        )

    def list_accounts(self, limit: int = 200) -> list[str]:
        """获取账本中的账户列表。"""
        if not self.ledger_exists():
            return []
        try:
            result = self.execute(
                f'SELECT DISTINCT account ORDER BY account LIMIT {int(limit)}',
                enrich=False,
                meter=False,
            )
            accounts = []
            for line in result.result_text.splitlines():
                line = line.strip()
                if line and not line.startswith('account') and not set(line) <= {'-', ' '}:
                    accounts.append(line.split()[0])
            return accounts
        except Exception:
            logger.exception('获取账户列表失败')
            return []
