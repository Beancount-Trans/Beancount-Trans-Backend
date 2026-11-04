"""trans/ 条目迁移到月度账本的提交服务

用于 Git 用户把 trans/ 下已审核的条目按交易日期归入仓库根的月度文件
（{year}/{MM}.bean），提交并推送到远程，使其可通过 git pull 取回。

本服务**不做条目去重**：去重是写入/审核阶段的职责，提交阶段只负责把
trans/ 中已有条目按月份归位。
"""
import logging
import os
from typing import Any, Dict, List

from project.utils.file import BeanFileManager
from .services import PlatformGitService, GitServiceException

logger = logging.getLogger(__name__)


class LedgerCommitService:
    """trans/ → 月度账本迁移提交服务（全 classmethod）"""

    @classmethod
    def _collect_entries(cls, user) -> Dict[str, Any]:
        """扫描 trans/ 下全部 .bean（排除索引 main.bean），按 YYYY-MM 分桶收集条目"""
        buckets: Dict[str, List[str]] = {}
        files = BeanFileManager.iter_trans_bean_files(user, exclude_index=True)
        errors: List[Dict[str, str]] = []

        for rel_path in files:
            abs_path = BeanFileManager._resolve_trans_path(user, rel_path)
            try:
                with open(abs_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except OSError as exc:
                errors.append({'file': rel_path, 'error': str(exc)})
                continue

            for date_key, block in BeanFileManager.split_entries(content):
                buckets.setdefault(date_key, []).append(block)

        return {'buckets': buckets, 'files': files, 'errors': errors}

    @classmethod
    def _build_plan(cls, buckets: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        """构建迁移计划：每个目标月份的条目数"""
        plans: List[Dict[str, Any]] = []
        for date_key in sorted(buckets.keys()):
            year = int(date_key[:4])
            month = int(date_key[5:7])
            plans.append({
                'year': year,
                'month': month,
                'target': BeanFileManager.get_monthly_relative_path(year, month),
                'count': len(buckets[date_key]),
            })
        return plans

    @classmethod
    def preview(cls, user) -> Dict[str, Any]:
        """预览迁移计划，不落盘、不提交

        Returns:
            Dict[str, Any]: 含 total_entries / files_scanned / plans / errors
        """
        if not hasattr(user, 'git_repo'):
            raise GitServiceException("用户未启用 Git 功能")

        collected = cls._collect_entries(user)
        plans = cls._build_plan(collected['buckets'])
        total_entries = sum(len(blocks) for blocks in collected['buckets'].values())

        return {
            'total_entries': total_entries,
            'files_scanned': len(collected['files']),
            'plans': plans,
            'errors': collected['errors'],
        }

    @classmethod
    def execute(cls, user) -> Dict[str, Any]:
        """执行迁移：写入月度文件 → 提交推送 → 清空 trans/ 条目

        推送失败时回滚已写入的月度文件，保证重试不会重复写入。

        Raises:
            GitServiceException: 未启用 Git，或存在解析失败文件（整单中止）
        """
        if not hasattr(user, 'git_repo'):
            raise GitServiceException("用户未启用 Git 功能")

        collected = cls._collect_entries(user)

        if collected['errors']:
            first = collected['errors'][0]
            raise GitServiceException(
                f"存在无法解析的文件，已中止迁移: {first['file']} ({first['error']})"
            )

        buckets = collected['buckets']
        if not buckets:
            # trans/ 无新条目时，仍尝试补推此前失败的平台提交（如账户币种改动）
            push_result = None
            repo_path = BeanFileManager.get_user_assets_path(user)
            if os.path.isdir(os.path.join(repo_path, '.git')):
                push_result = PlatformGitService().push_pending(user)

            return {
                'status': (push_result or {}).get('status', 'skipped'),
                'message': (
                    (push_result or {}).get('message')
                    or 'trans/ 下没有可迁移的条目'
                ),
                'plans': [],
                'entries_appended': 0,
                'files_cleared': 0,
                'push': push_result,
            }

        plans = cls._build_plan(buckets)
        years = sorted({int(key[:4]) for key in buckets})

        # 先确保年度结构，再快照各月度文件内容（用于推送失败回滚）
        for year in years:
            BeanFileManager.ensure_year_structure(user, year)

        snapshots: Dict[str, str] = {}
        for date_key in sorted(buckets.keys()):
            year = int(date_key[:4])
            month = int(date_key[5:7])
            month_path = BeanFileManager.get_monthly_bean_path(user, year, month)
            if os.path.exists(month_path):
                with open(month_path, 'r', encoding='utf-8') as f:
                    snapshots[month_path] = f.read()
            else:
                snapshots[month_path] = ''

        total_appended = 0
        for date_key in sorted(buckets.keys()):
            year = int(date_key[:4])
            month = int(date_key[5:7])
            total_appended += BeanFileManager.append_entries_to_monthly(
                user, year, month, buckets[date_key]
            )

        # 提交推送：仅包含受影响年度目录与根 main.bean
        paths = [f"{year}/" for year in years] + ['main.bean']
        try:
            push_result = PlatformGitService().push_ledger(user, paths=paths)
        except Exception:
            for month_path, content in snapshots.items():
                with open(month_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            logger.warning(
                "Ledger commit push failed for %s, rolled back monthly files",
                getattr(user, 'username', user),
            )
            raise

        # 推送成功后才清空 trans/，失败则条目保留以便重试
        files_cleared = BeanFileManager.clear_trans_entries(user)

        logger.info(
            "Ledger commit for %s: appended=%d cleared=%d push=%s",
            getattr(user, 'username', user),
            total_appended,
            files_cleared,
            push_result.get('status'),
        )

        return {
            'status': 'success',
            'message': '已迁移到月度账本并推送到远程仓库',
            'plans': plans,
            'entries_appended': total_appended,
            'files_cleared': files_cleared,
            'push': push_result,
        }
