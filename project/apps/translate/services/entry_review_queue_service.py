# project/apps/translate/services/entry_review_queue_service.py
"""
用户级统一审核队列服务

每个用户维护一个全局唯一的审核队列（Redis list），仅保存
{source, file_id, uuid} 形式的顺序引用；条目内容真源仍在各文件的
parse_result:{file_id} 缓存中（由 ParseReviewService 管理），
Copilot 记账条目则来自用户级暂存区（键 parse_result:copilot:{user_id}）。
"""
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from django.core.cache import cache

from project.apps.translate.services.parse_review_service import ParseReviewService

logger = logging.getLogger(__name__)


class EntryReviewQueueService:
    """用户级统一审核队列服务"""

    CACHE_KEY_PREFIX = 'entry_review_queue'
    LOCK_KEY_PREFIX = 'entry_review_lock'
    DEFAULT_TTL = 25 * 3600

    SOURCE_FILE = 'file'
    SOURCE_COPILOT = 'copilot'
    SOURCE_RECURRING = 'recurring'

    @classmethod
    def _queue_key(cls, user_id: int) -> str:
        """生成用户审核队列的缓存键"""
        return f'{cls.CACHE_KEY_PREFIX}:{user_id}'

    @classmethod
    def _lock_key(cls, user_id: int) -> str:
        """生成用户审核队列锁的缓存键"""
        return f'{cls.LOCK_KEY_PREFIX}:{user_id}'

    # ------------------------------------------------------------------
    # 引用来源
    # ------------------------------------------------------------------
    @classmethod
    def ref_source(cls, ref: Dict[str, Any]) -> str:
        """引用来源；历史引用缺少 source 时按账单文件处理。"""
        return str((ref or {}).get('source') or cls.SOURCE_FILE)

    @classmethod
    def _ref_still_valid(cls, user_id: int, ref: Dict[str, Any]) -> bool:
        """引用的条目真源是否仍存在（缓存是否存在，不校验 uuid）。"""
        if cls.ref_source(ref) == cls.SOURCE_COPILOT:
            from project.apps.translate.services.copilot_bookkeeping_service import (
                CopilotBookkeepingService,
            )

            return CopilotBookkeepingService.has_staging(user_id)
        if cls.ref_source(ref) == cls.SOURCE_RECURRING:
            from project.apps.translate.services.recurring_bookkeeping_service import (
                RecurringBookkeepingService,
            )

            return RecurringBookkeepingService.has_staging(user_id)
        file_id = ref.get('file_id')
        if file_id is None:
            return False
        return ParseReviewService.get_parse_result(file_id) is not None

    # ------------------------------------------------------------------
    # 用户级锁（串行化多文件并发解析时的合并/去重）
    # ------------------------------------------------------------------
    @classmethod
    def acquire_lock(
        cls,
        user_id: int,
        timeout: int = 30,
        wait: float = 5.0,
        interval: float = 0.1,
    ) -> bool:
        """尝试获取用户级锁；成功返回 True，超时返回 False。

        Args:
            user_id: 用户 ID
            timeout: 锁的持有时间（秒）
            wait: 获取失败时的最长等待时间（秒）
            interval: 轮询重试间隔（秒）
        """
        key = cls._lock_key(user_id)
        token = uuid.uuid4().hex
        # cache.add 仅在键不存在时写入，天然具备原子性
        if cache.add(key, token, timeout=timeout):
            return True

        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(interval)
            if cache.add(key, token, timeout=timeout):
                return True
        return False

    @classmethod
    def release_lock(cls, user_id: int) -> None:
        """释放用户级锁（直接删除键，简单可靠）。"""
        cache.delete(cls._lock_key(user_id))

    @classmethod
    def _get_refs(cls, user_id: int) -> List[Dict[str, Any]]:
        """读取原始引用列表（不做有效性校验）"""
        refs = cache.get(cls._queue_key(user_id))
        if not refs:
            return []
        # 兼容缓存后端返回字符串的场景
        if isinstance(refs, str):
            try:
                refs = json.loads(refs)
            except json.JSONDecodeError:
                logger.error('解析条目审核队列缓存失败: %s', cls._queue_key(user_id))
                return []
        if not isinstance(refs, list):
            return []
        return refs

    @classmethod
    def _save_refs(
        cls,
        user_id: int,
        refs: List[Dict[str, Any]],
        timeout: Optional[int] = None,
    ) -> bool:
        """写回引用列表，默认刷新为 DEFAULT_TTL"""
        if timeout is None:
            timeout = cls.DEFAULT_TTL
        try:
            cache.set(cls._queue_key(user_id), refs, timeout=timeout)
            return True
        except Exception as e:
            logger.error('保存条目审核队列失败: %s, 错误: %s', cls._queue_key(user_id), str(e))
            return False

    @classmethod
    def list_refs(cls, user_id: int) -> List[Dict[str, Any]]:
        """返回有效引用；读取时顺带剔除缓存已过期的引用"""
        refs = cls._get_refs(user_id)
        valid_refs: List[Dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get('uuid') is None:
                continue
            # 条目真源缓存已失效，说明该引用已不可审核，剔除
            if not cls._ref_still_valid(user_id, ref):
                continue
            valid_refs.append(ref)
        if len(valid_refs) != len(refs):
            cls._save_refs(user_id, valid_refs)
        return valid_refs

    @classmethod
    def enqueue(cls, user_id: int, refs: List[Dict[str, Any]]) -> int:
        """把引用追加到队列，按 (source, file_id, uuid) 去重并保持顺序，返回新增数量"""
        existing = cls._get_refs(user_id)
        seen = {
            (cls.ref_source(ref), ref.get('file_id'), ref.get('uuid'))
            for ref in existing
        }
        added = 0
        for ref in refs:
            source = cls.ref_source(ref)
            file_id = ref.get('file_id')
            entry_uuid = ref.get('uuid')
            if entry_uuid is None:
                continue
            # 账单文件引用必须有 file_id；copilot 引用允许 file_id 为 None
            if source == cls.SOURCE_FILE and file_id is None:
                continue
            key = (source, file_id, entry_uuid)
            if key in seen:
                continue
            stored = {'file_id': file_id, 'uuid': entry_uuid}
            if ref.get('source'):
                stored = {'source': source, 'file_id': file_id, 'uuid': entry_uuid}
            existing.append(stored)
            seen.add(key)
            added += 1
        # 即使没有新增也写回，用于刷新 TTL
        cls._save_refs(user_id, existing)
        return added

    @classmethod
    def remove_file(cls, user_id: int, file_id: int) -> None:
        """移除指定账单文件（source='file'）的所有引用，不影响 copilot 来源"""
        refs = cls._get_refs(user_id)
        remaining = [
            ref for ref in refs
            if not (
                cls.ref_source(ref) == cls.SOURCE_FILE
                and ref.get('file_id') == file_id
            )
        ]
        if len(remaining) != len(refs):
            cls._save_refs(user_id, remaining)

    @classmethod
    def remove_entries(cls, user_id: int, refs: List[Dict[str, Any]]) -> None:
        """移除列表中指定的 (source, file_id, uuid) 引用"""
        keys = {
            (cls.ref_source(ref), ref.get('file_id'), ref.get('uuid'))
            for ref in refs
        }
        existing = cls._get_refs(user_id)
        remaining = [
            ref for ref in existing
            if (cls.ref_source(ref), ref.get('file_id'), ref.get('uuid')) not in keys
        ]
        if len(remaining) != len(existing):
            cls._save_refs(user_id, remaining)

    @classmethod
    def is_empty(cls, user_id: int) -> bool:
        """判断用户审核队列是否为空"""
        return len(cls.list_refs(user_id)) == 0

    @classmethod
    def clear(cls, user_id: int) -> None:
        """删除整个队列键"""
        cache.delete(cls._queue_key(user_id))

    @classmethod
    def earliest_expires_at(cls, user_id: int) -> Optional[float]:
        """返回队列中所有有效条目的最早审核截止时间，无有效引用返回 None"""
        refs = cls.list_refs(user_id)
        expires_values: List[float] = []

        if any(cls.ref_source(ref) == cls.SOURCE_COPILOT for ref in refs):
            from project.apps.translate.services.copilot_bookkeeping_service import (
                CopilotBookkeepingService,
            )

            expires_at = CopilotBookkeepingService.expires_at(user_id)
            if expires_at is not None:
                expires_values.append(expires_at)

        for ref in refs:
            if cls.ref_source(ref) in (cls.SOURCE_COPILOT, cls.SOURCE_RECURRING):
                continue
            cached_data = ParseReviewService.get_parse_result(ref.get('file_id'))
            if cached_data is None:
                continue
            expires_at = ParseReviewService.get_review_expires_at(cached_data, None)
            if expires_at is not None:
                expires_values.append(expires_at)

        if not expires_values:
            return None
        return min(expires_values)

    @classmethod
    def list_entries(cls, user_id: int) -> List[Dict[str, Any]]:
        """按队列顺序返回待审核条目列表（补充 source / file_id / file_name）"""
        from project.apps.translate.models import ParseFile

        entries: List[Dict[str, Any]] = []
        for ref in cls.list_refs(user_id):
            entry_uuid = ref.get('uuid')
            if cls.ref_source(ref) == cls.SOURCE_COPILOT:
                from project.apps.translate.services.copilot_bookkeeping_service import (
                    CopilotBookkeepingService,
                )

                entry = CopilotBookkeepingService.get_entry(user_id, entry_uuid)
                if entry is not None:
                    entries.append(entry)
                continue

            if cls.ref_source(ref) == cls.SOURCE_RECURRING:
                from project.apps.translate.services.recurring_bookkeeping_service import (
                    RecurringBookkeepingService,
                )

                # 读取时续期，保证未确认的循环条目长期保留
                RecurringBookkeepingService.touch(user_id)
                entry = RecurringBookkeepingService.get_entry(user_id, entry_uuid)
                if entry is not None:
                    entries.append(entry)
                continue

            file_id = ref.get('file_id')
            cached_data = ParseReviewService.get_parse_result_migrated(file_id)
            if cached_data is None:
                continue
            target = None
            for entry in cached_data.get('formatted_data') or []:
                if entry.get('uuid') == entry_uuid:
                    target = entry
                    break
            if target is None:
                continue
            parse_file = (
                ParseFile.objects
                .filter(file_id=file_id)
                .select_related('file')
                .first()
            )
            file_name = parse_file.file.name if parse_file else None
            # 复制条目后再补充字段，避免污染 Redis 缓存中的条目结构
            item = dict(target)
            item['source'] = cls.SOURCE_FILE
            item['file_id'] = int(file_id)
            item['file_name'] = file_name
            entries.append(item)
        return entries

    @classmethod
    def count_needs_review(cls, user_id: int, fallback_account=None) -> int:
        """统计需要人工审核的条目数（兜底账户或结构异常）。"""
        from project.apps.translate.services.entry_review_attention import (
            count_needs_review,
        )

        return count_needs_review(cls.list_entries(user_id), fallback_account)

    # ------------------------------------------------------------------
    # 待办辅助方法
    # ------------------------------------------------------------------
    @classmethod
    def get_or_create_task(cls, user):
        """获取或创建该用户的全局唯一条目审核待办"""
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth import get_user_model
        from project.apps.reconciliation.models import ScheduledTask

        content_type = ContentType.objects.get_for_model(get_user_model())
        task, _ = ScheduledTask.objects.get_or_create(
            content_type=content_type,
            object_id=user.id,
            task_type='entry_review',
            defaults={'scheduled_date': None, 'status': 'inactive'},
        )
        return task

    @classmethod
    def activate_task(cls, user):
        """激活待办（status='pending'）"""
        task = cls.get_or_create_task(user)
        task.status = 'pending'
        task.save()
        return task

    @classmethod
    def complete_task(cls, user) -> None:
        """完成待办（若存在）"""
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth import get_user_model
        from project.apps.reconciliation.models import ScheduledTask

        content_type = ContentType.objects.get_for_model(get_user_model())
        task = ScheduledTask.objects.filter(
            content_type=content_type,
            object_id=user.id,
            task_type='entry_review',
        ).first()
        if task is not None:
            task.status = 'completed'
            task.save()

    @classmethod
    def deactivate_if_empty(cls, user) -> None:
        """队列为空时把待办置为未激活（若任务存在）"""
        if not cls.is_empty(user.id):
            return
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth import get_user_model
        from project.apps.reconciliation.models import ScheduledTask

        content_type = ContentType.objects.get_for_model(get_user_model())
        task = ScheduledTask.objects.filter(
            content_type=content_type,
            object_id=user.id,
            task_type='entry_review',
        ).first()
        if task is not None:
            task.status = 'inactive'
            task.save()
