# project/apps/translate/tasks.py
from celery import shared_task
from django.core.cache import cache
from project.apps.translate.models import ParseFile
# from project.apps.file_manager.models import File
from project.utils.storage_factory import get_storage_client
# from django.conf import settings
# from project.utils.file import BeanFileManager
from project.apps.translate.services.analyze_service import AnalyzeService
from project.apps.translate.services.parse_review_service import ParseReviewService
# from project.apps.translate.utils import get_user_config
from project.utils.tools import get_user_config
import logging
import time
# import json

logger = logging.getLogger(__name__)


def _record_parse_usage(task_id, user_id, status, entries=0, error=''):
    """记录解析用量（延迟导入，避免 app 加载顺序问题；内部已吞异常）。"""
    from project.apps.usage.services import record_parse

    record_parse(
        task_id=task_id,
        user_id=user_id,
        status=status,
        entries=entries,
        error=error,
    )


@shared_task(bind=True)
def parse_single_file_task(self, file_id, user_id, args):
    task_id = self.request.id

    try:
        # 获取ParseFile对象，检查是否已被取消
        parse_file = ParseFile.objects.get(file_id=file_id)
        if parse_file.status == 'cancelled':
            # 更新Redis状态
            cache.set(f'task_status:{task_id}', {
                'status': 'cancelled',
                'file_id': file_id,
                'error': None
            }, timeout=24*3600)
            return {'status': 'cancelled', 'file_id': file_id}

        # 更新Redis状态为processing
        cache.set(f'task_status:{task_id}', {
            'status': 'processing',
            'file_id': file_id,
            'error': None
        }, timeout=24*3600)

        # 更新状态为processing
        parse_file.status = 'processing'
        parse_file.save()

        # {"group_id": "988da899-5ec0-4af1-8568-db81afa6bbbf", "created_at": 1754963160.402835, "file_ids": [26, 27], "task_ids": [null, null], "status": "processing"}
        # 获取文件对象

        file_obj = parse_file.file
        # 账本目录镜像平台目录结构：把文件在平台中的相对目录注入解析上下文
        args['bean_relative_dir'] = file_obj.get_bean_dir()
        storage_client = get_storage_client()

        # 从存储获取文件内容
        file_data = storage_client.download_file(file_obj.storage_name)
        if file_data is None:
            raise Exception(f"文件不存在: {file_obj.storage_name}")

        file_content = file_data.read()

        # 创建模拟文件对象
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        file_stream = BytesIO(file_content)
        uploaded_file = InMemoryUploadedFile(
            file_stream,
            None,  # field_name
            file_obj.name,
            file_obj.content_type,
            len(file_content),
            None  # charset
        )

        # 解析文件
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(id=user_id)
        config = get_user_config(user)
        # args['write'] 的值由 MultiBillAnalyzeView 根据用户偏好设置

        service = AnalyzeService(user=user, config=config)
        result_context = service.analyze_single_file(uploaded_file, args)

        status = result_context.get('status', '')
        errors = result_context.get('errors', [])
        formatted_data = result_context.get('formatted_data', [])
        parsed_data = result_context.get('parsed_data', [])

        # 解析失败（管线内 _error）：不创建空待办，区分解密与不支持/失败
        if status == 'error':
            from project.utils.exceptions import DecryptionError
            errors_text = ' '.join(str(e) for e in errors) if errors else ''
            # 解密相关：显式关键词 或 加密 PDF 提取失败时的典型错误（NoneType+bytes 等）
            is_decryption = (
                any(kw in errors_text for kw in ('解密', 'Decryption', 'PDF解密', 'ZIP 解密', '解密失败'))
                or ('NoneType' in errors_text and 'bytes' in errors_text and 'unsupported operand' in errors_text)
            )
            if is_decryption:
                raise DecryptionError(errors[-1] if errors else '解密失败，请提供密码后重试')
            parse_file.status = 'failed'
            parse_file.error_message = errors[-1] if errors else '解析失败'
            parse_file.save()
            _record_parse_usage(task_id, user_id, 'failed', error=parse_file.error_message)
            cache.set(f'task_status:{task_id}', {
                'status': 'failed',
                'file_id': file_id,
                'error': parse_file.error_message
            }, timeout=24*3600)
            return {'status': 'failed', 'file_id': file_id, 'error': parse_file.error_message}

        # 格式与内容均支持但过滤后无有效交易：不创建解析待办
        if len(formatted_data) == 0:
            parse_file.status = 'failed'
            parse_file.error_message = '未解析到有效条目'
            parse_file.save()
            _record_parse_usage(task_id, user_id, 'failed', error=parse_file.error_message)
            cache.set(f'task_status:{task_id}', {
                'status': 'failed',
                'file_id': file_id,
                'error': parse_file.error_message
            }, timeout=24*3600)
            return {'status': 'failed', 'file_id': file_id, 'error': parse_file.error_message}

        # 根据 write 标志决定处理方式（仅在有有效解析结果时执行）
        should_write = args.get('write', True)
        
        if not should_write:
            # 审核模式：不写入文件，存入缓存，激活待办
            # 为每条记录补充 uuid 和 original_row
            # 从 parsed_data 中查找对应的记录
            parsed_data_dict = {entry.get('cache_key'): entry for entry in parsed_data}
            
            # 从 CacheStep 的缓存中获取 original_row
            enhanced_formatted_data = []
            for entry in formatted_data:
                cache_key = entry.get('id')  # FormatStep 输出的 id 就是 cache_key
                parsed_entry = parsed_data_dict.get(cache_key, {})
                
                # 从 CacheStep 的缓存中获取 original_row
                cache_entry_data = cache.get(cache_key)
                original_row = None
                cached_parsed = {}
                if cache_entry_data and isinstance(cache_entry_data, dict):
                    original_row = cache_entry_data.get('original_row')
                    cached_parsed = cache_entry_data.get('parsed_entry') or {}
                if not parsed_entry and cached_parsed:
                    parsed_entry = cached_parsed
                tag_details = parsed_entry.get('tag_details') or cached_parsed.get('tag_details', [])
                
                enhanced_entry = {
                    # 审核条目身份必须用 cache_key：交易订单号（parsed uuid）可能重复。
                    # 无订单号时 cache_key 为 md5；parsed_entry['uuid'] 可能为 None，不能用 .get('uuid', cache_key)。
                    'uuid': cache_key or parsed_entry.get('uuid'),
                    'formatted': entry.get('formatted', ''),
                    'edited_formatted': entry.get('formatted', ''),  # 初始状态默认为 formatted
                    'selected_expense_key': entry.get('selected_expense_key', ''),
                    'expense_candidates_with_score': entry.get('expense_candidates_with_score', []),
                    'original_row': original_row,
                    'tag_details': tag_details,
                    'tag_overrides': ParseReviewService.default_tag_overrides(),
                    'installment_role': parsed_entry.get('installment_role') or entry.get('installment_role'),
                    'installment_period': parsed_entry.get('installment_period') if parsed_entry.get('installment_period') is not None else entry.get('installment_period'),
                }
                enhanced_formatted_data.append(enhanced_entry)
            
            # 准备缓存数据
            now = time.time()
            cache_data = {
                'file_id': file_id,
                'formatted_data': enhanced_formatted_data,
                'created_at': now,
                'review_expires_at': now + ParseReviewService.REVIEW_DEADLINE_SECONDS,
            }
            
            # 保存到 Redis 缓存
            ParseReviewService.save_parse_result(
                file_id,
                cache_data,
                timeout=ParseReviewService.DEFAULT_CACHE_TIMEOUT,
            )
            
            # 合并进用户级统一审核队列 + 入队前去重
            from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
            from project.apps.translate.services.entry_dedup_service import EntryDedupService

            acquired = EntryReviewQueueService.acquire_lock(user_id)
            try:
                if acquired:
                    # 重新解析场景：先移除该文件在队列中的旧引用，避免自比对
                    EntryReviewQueueService.remove_file(user_id, file_id)
                    existing_entries = EntryReviewQueueService.list_entries(user_id)
                    kept, duplicates = EntryDedupService.dedup_new_entries(
                        user, enhanced_formatted_data, existing_entries
                    )
                else:
                    # 未拿到锁：跳过去重，全部入队，避免条目丢失
                    logger.warning(
                        '未获取到条目审核队列锁，跳过去重: file_id=%s, user_id=%s',
                        file_id, user_id,
                    )
                    kept, duplicates = list(enhanced_formatted_data), []

                if duplicates:
                    dup_uuids = [e.get('uuid') for e in duplicates if e.get('uuid')]
                    if dup_uuids:
                        ParseReviewService.remove_entries(file_id, dup_uuids)

                refs = [
                    {'file_id': file_id, 'uuid': e.get('uuid')}
                    for e in kept if e.get('uuid')
                ]
                if refs:
                    EntryReviewQueueService.enqueue(user_id, refs)
                    EntryReviewQueueService.activate_task(user)

                # 有保留条目 -> 待审核；全部被去重 -> 已解析
                final_status = 'pending_review' if refs else 'parsed'
                parse_file.status = final_status
                parse_file.save()
                _record_parse_usage(task_id, user_id, 'ok', entries=len(kept))
            finally:
                if acquired:
                    EntryReviewQueueService.release_lock(user_id)

            # 更新 Redis 状态
            cache.set(f'task_status:{task_id}', {
                'status': final_status,
                'file_id': file_id,
                'error': None
            }, timeout=24*3600)

            return {
                'status': final_status,
                'file_id': file_id
            }
        else:
            # 直接写入模式：保持原有逻辑
            # 更新状态
            parse_file.status = 'parsed'
            parse_file.save()
            _record_parse_usage(task_id, user_id, 'ok', entries=len(formatted_data))

            # 更新状态为parsed
            cache.set(f'task_status:{task_id}', {
                'status': 'parsed',
                'file_id': file_id,
                'error': None
            }, timeout=24*3600)

            return {
                'status': 'parsed',
                'file_id': file_id
            }

    except Exception as e:
        logger.error(f"文件解析失败: {file_id}, 错误: {str(e)}")
        parse_file = ParseFile.objects.get(file_id=file_id)

        # 检查是否为密码错误导致的解析失败
        from project.utils.exceptions import DecryptionError
        if isinstance(e, DecryptionError) or '解密失败' in str(e):
            parse_file.status = 'needs_password'
            cache_status = 'needs_password'
        else:
            parse_file.status = 'failed'
            cache_status = 'failed'

        parse_file.error_message = str(e)
        parse_file.save()
        _record_parse_usage(task_id, user_id, 'failed', error=str(e))
        
        # 更新 Redis 状态
        cache.set(f'task_status:{task_id}', {
            'status': cache_status,
            'file_id': file_id,
            'error': str(e)
        }, timeout=24*3600)

        return {
            'status': cache_status,
            'file_id': file_id,
            'error': str(e)
        }


@shared_task
def auto_confirm_expired_entry_reviews():
    """定时任务：到期自动确认写入

    每小时执行一次，扫描所有待执行的条目审核待办，逐个处理该用户统一
    审核队列中审核截止时间已过的条目，自动确认写入并从队列移除引用；
    队列为空时把待办标记为已完成。

    队列引用按来源（``ref['source']``，历史引用缺失时按账单文件处理）分派：

    - ``file``（账单文件来源）：按各文件缓存 ``review_expires_at`` 判断到期，
      校验通过后覆盖写入 ``trans/{账单名}.bean``，并把 ``ParseFile`` 置为
      ``parsed``；
    - ``copilot``（Copilot 记账来源）：按用户级暂存区截止时间判断到期，校验
      通过后追加写入 ``trans/collect.bean``（不覆盖已有内容），随后删除暂存区。
    - ``recurring``（循环待办来源）：**永不过期、不自动写入**，始终跳过，
      保留引用直到用户手动确认。

    单个用户或来源处理异常只记录日志并继续处理其他队列，不中断整个任务。
    """
    from django.contrib.auth import get_user_model
    from project.apps.reconciliation.models import ScheduledTask
    from django.contrib.contenttypes.models import ContentType
    from project.apps.translate.services.parse_review_service import ParseReviewService
    from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
    from project.apps.translate.services.copilot_bookkeeping_service import CopilotBookkeepingService
    from project.apps.translate.utils.beancount_validator import BeancountValidator
    from project.utils.file import BeanFileManager
    
    logger.info("开始执行到期自动确认写入任务")
    
    # 获取所有待执行的条目审核待办（关联到 User 模型）
    content_type = ContentType.objects.get_for_model(get_user_model())
    pending_tasks = ScheduledTask.objects.filter(
        task_type='entry_review',
        status='pending',
        content_type=content_type
    )
    
    now = time.time()
    confirmed_count = 0
    error_count = 0
    
    for task in pending_tasks:
        user = task.content_object
        if user is None:
            continue

        # 按 (source, file_id) 分组，保持队列中首次出现的顺序
        groups = {}
        for ref in EntryReviewQueueService.list_refs(user.id):
            source = EntryReviewQueueService.ref_source(ref)
            groups.setdefault((source, ref.get('file_id')), []).append(ref)

        user_entries_written = 0

        for (source, file_id), group_refs in groups.items():
            try:
                if source == EntryReviewQueueService.SOURCE_RECURRING:
                    # 循环待办来源：永不过期、不自动写入，保留引用等待用户确认
                    continue
                if source == EntryReviewQueueService.SOURCE_COPILOT:
                    # ---- Copilot 记账来源：暂存区到期后追加写入 collect.bean ----
                    staging_key = CopilotBookkeepingService.staging_key(user.id)
                    cached_data = ParseReviewService.get_parse_result(staging_key)
                    if not cached_data:
                        logger.warning(f"Copilot 暂存区不存在或已过期，移除引用: user_id={user.id}")
                        EntryReviewQueueService.remove_entries(user.id, group_refs)
                        continue

                    if not ParseReviewService.is_review_expired(cached_data, None, now=now):
                        continue

                    final_entries = ParseReviewService.get_final_result(staging_key)
                    if not final_entries:
                        logger.warning(f"Copilot 暂存区无有效条目，移除引用: user_id={user.id}")
                        EntryReviewQueueService.remove_entries(user.id, group_refs)
                        continue

                    # 合并所有条目
                    formatted_text = '\n\n'.join([
                        entry['formatted'].rstrip() for entry in final_entries
                    ])

                    # 进行 Beancount 语法校验
                    is_valid, error_message, _ = BeancountValidator.validate_entries(formatted_text)
                    if not is_valid:
                        # 逐条校验以定位具体错误条目并记录日志
                        entries_list = [e['formatted'].rstrip() for e in final_entries]
                        _, _, error_entries_indices = BeancountValidator.validate_multiple_entries(entries_list)
                        error_details = [
                            f"index={idx} uuid={final_entries[idx].get('uuid', '?')}: {msg}"
                            for idx, msg in error_entries_indices
                        ]
                        logger.error(
                            "Beancount 语法错误，跳过 Copilot 自动写入: user_id=%s, error=%s, 错误条目: %s",
                            user.id,
                            error_message,
                            "; ".join(error_details),
                        )
                        error_count += 1
                        continue

                    # 追加写入 trans/collect.bean（不覆盖已有内容）
                    BeanFileManager.append_to_collect_bean(
                        user, [entry['formatted'] for entry in final_entries]
                    )
                    # 删除暂存区并从队列移除该组引用
                    CopilotBookkeepingService.clear(user.id)
                    EntryReviewQueueService.remove_entries(user.id, group_refs)

                    confirmed_count += 1
                    user_entries_written += len(final_entries)
                    logger.info(
                        "Copilot 自动确认写入成功: user_id=%s, 条数=%s, task_id=%s",
                        user.id, len(final_entries), task.id,
                    )
                    continue

                # ---- 账单文件来源：保持原有覆盖写入逻辑 ----
                cached_data = ParseReviewService.get_parse_result(file_id)
                if not ParseReviewService.is_review_expired(cached_data, task, now=now):
                    continue

                # 从缓存获取最终结果
                final_entries = ParseReviewService.get_final_result(file_id)

                if not final_entries:
                    logger.warning(f"解析结果不存在或已过期: file_id={file_id}")
                    # 如果缓存已过期，直接标记为已解析并从队列移除（避免重复处理）
                    parse_file = ParseFile.objects.filter(file_id=file_id).select_related('file').first()
                    if parse_file is not None:
                        parse_file.status = 'parsed'
                        parse_file.save()
                    EntryReviewQueueService.remove_file(user.id, file_id)
                    confirmed_count += 1
                    continue

                # 合并所有条目
                formatted_text = '\n\n'.join([
                    entry['formatted'].rstrip() for entry in final_entries
                ])

                # 进行 Beancount 语法校验
                is_valid, error_message, _ = BeancountValidator.validate_entries(formatted_text)

                if not is_valid:
                    # 逐条校验以定位具体错误条目并记录日志
                    entries_list = [e['formatted'].rstrip() for e in final_entries]
                    _, _, error_entries_indices = BeancountValidator.validate_multiple_entries(entries_list)
                    error_details = [
                        f"index={idx} uuid={final_entries[idx].get('uuid', '?')}: {msg}"
                        for idx, msg in error_entries_indices
                    ]
                    logger.error(
                        "Beancount 语法错误，跳过自动确认: file_id=%s, error=%s, 错误条目: %s",
                        file_id,
                        error_message,
                        "; ".join(error_details),
                    )
                    error_count += 1
                    continue

                # 写入文件
                parse_file = ParseFile.objects.filter(file_id=file_id).select_related('file').first()
                if parse_file is None:
                    logger.error(f"解析文件记录不存在，跳过自动确认: file_id={file_id}")
                    error_count += 1
                    continue

                bean_file_path = BeanFileManager.get_bean_file_path(
                    user, parse_file.file.name, parse_file.file.get_bean_dir()
                )

                with open(bean_file_path, 'w', encoding='utf-8') as f:
                    f.write(formatted_text)

                # 更新状态
                parse_file.status = 'parsed'
                parse_file.save()

                # 从用户统一审核队列中移除该文件引用
                EntryReviewQueueService.remove_file(user.id, file_id)

                confirmed_count += 1
                user_entries_written += len(final_entries)
                logger.info(
                    "账单文件自动确认写入成功: source=file, file_id=%s, 条数=%s, task_id=%s",
                    file_id, len(final_entries), task.id,
                )

            except Exception as e:
                logger.error(
                    f"自动确认写入失败: source={source}, file_id={file_id}, "
                    f"user_id={user.id}, task_id={task.id}, error={str(e)}",
                    exc_info=True,
                )
                error_count += 1
                continue

        logger.info(
            "用户到期自动写入处理完成: user_id=%s, 写入条数=%s",
            user.id, user_entries_written,
        )

        # 写入后维护对账条目：为被后补交易打破的 balance 断言补 pad，
        # 并注释因真实交易落入断言容差而变为无用的 pad（best-effort）
        from project.apps.reconciliation.services.reconciliation_comment_service import (
            ReconciliationCommentService,
        )
        ReconciliationCommentService.maintain_ledger(user)

        # 该用户队列已清空，完成待办；否则保持 pending
        if EntryReviewQueueService.is_empty(user.id):
            EntryReviewQueueService.complete_task(user)
    
    logger.info(f"到期自动确认写入任务完成: 成功={confirmed_count}, 失败={error_count}")
    return {
        'confirmed_count': confirmed_count,
        'error_count': error_count
    }