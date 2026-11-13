# project/apps/translate/views/views.py
import logging
import uuid
import json
import time
import os
from typing import Dict, Optional
from celery.result import GroupResult
from celery import group
from django.core.cache import cache
from django.shortcuts import render
from django.contrib.auth import get_user_model
from project.utils.exceptions import UnsupportedFileTypeError, DecryptionError
from project.utils.token import get_token_user_id, has_valid_token
from project.utils.tools import get_user_config
from project.apps.common.permissions import IsOwnerOrAdminReadWriteOnly, AnonymousReadOnlyPermission
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication
from project.apps.translate.models import FormatConfig, ParseFile
from project.apps.translate.serializers import AnalyzeSerializer, FormatConfigSerializer, ReparseSerializer
from project.apps.translate.utils import FormatData
from project.apps.translate.tasks import parse_single_file_task
from project.apps.translate.services.analyze_service import AnalyzeService
from project.apps.translate.services.bill_support import build_bill_support_snapshot
from project.apps.translate.services.parse.transaction_parser import single_parse_transaction
from project.apps.translate.services.parse.installment_expander import (
    expand_parsed_entry,
    pick_reparse_slice,
    resolve_reparse_entry,
)
from project.apps.translate.services.alipay_refund_peer import resolve_refund_peer_for_row
from project.apps.reconciliation.models import ScheduledTask
from django.contrib.contenttypes.models import ContentType
from project.apps.translate.utils import FormatData



User = get_user_model()
logger = logging.getLogger(__name__)


def _friendly_parse_error(raw) -> str:
    """去掉管道步骤前缀，返回可直接展示给用户的解析错误。"""
    text = str(raw)
    head, sep, tail = text.partition(': ')
    if sep and head.endswith('Step'):
        text = tail
    if text.startswith('文件转换失败: '):
        text = text[len('文件转换失败: '):]
    return text or '解析失败'


def _apply_parse_engine(config, parse_engine=None):
    """解析首页固定使用本地 BERT，个人配置中的解析引擎不参与。

    仅请求参数可按次切换到平台 LLM；未登录用户同样适用。
    """
    engine = (parse_engine or '').strip()
    config.ai_model = 'Platform' if engine == 'Platform' else 'BERT'
    return config


class BillAnalyzeView(APIView):
    """单账单解析接口

    Args:
        APIView (_type_): _description_

    Returns:
        _type_: _description_
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AnalyzeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        owner_id = get_token_user_id(request)
        config = _apply_parse_engine(
            get_user_config(User.objects.get(id=owner_id)),
            serializer.validated_data.get('parse_engine'),
        )

        uploaded_file = request.FILES.get('trans', None)
        if not uploaded_file:
            return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        service = AnalyzeService(owner_id, config)
        try:
            result = service.analyze(uploaded_file, serializer.validated_data)
            return Response(result, status=status.HTTP_200_OK)
        except DecryptionError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UnsupportedFileTypeError as e:
            return Response({'error': str(e)}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(e)
            return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def get(self, request):
        return render(request, "translate/trans.html", {"title": "trans"})


class UserConfigAPI(APIView):
    """用户个人配置接口
    
    支持匿名用户获取格式化输出配置（只读），匿名用户将获取 id=1 用户的配置。
    只有认证用户可以更新配置。
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [AnonymousReadOnlyPermission]

    def get(self, request):
        """获取当前用户配置（匿名用户获取 id=1 用户的配置）"""
        config = FormatConfig.get_user_config(request.user)
        serializer = FormatConfigSerializer(config)
        return Response(serializer.data)

    def put(self, request):
        """更新当前用户配置（需要认证）"""
        if not request.user.is_authenticated:
            return Response(
                {'error': '需要登录才能更新配置'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        config = FormatConfig.get_user_config(request.user)
        serializer = FormatConfigSerializer(
            config,
            data=request.data,
            partial=True  # 允许部分更新
        )

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)

        return Response({
            "status": "error",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class SingleBillAnalyzeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AnalyzeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 获取用户ID和配置
        # owner_id = get_token_user_id(request)
        user = User.objects.get(id=get_token_user_id(request))
        config = _apply_parse_engine(
            get_user_config(user),
            serializer.validated_data.get('parse_engine'),
        )

        # 获取上传文件
        uploaded_file = request.FILES.get('trans', None)
        if not uploaded_file:
            return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        # 创建服务并解析
        service = AnalyzeService(user=user, config=config)
        results = []
        try:
            # 匿名解析没有自己的账户体系，不写入平台账户/映射
            args = dict(serializer.validated_data)
            args['auto_provision_assets'] = has_valid_token(request)
            context = service.analyze_single_file(uploaded_file, args)
            if context.get('status') == 'error':
                code = context.get('error_code') or 'PARSE_FAILED'
                raw_error = (context.get('errors') or ['解析失败'])[-1]
                logger.warning(
                    '账单解析失败 user=%s file=%s code=%s error=%s',
                    user.id,
                    getattr(uploaded_file, 'name', ''),
                    code,
                    raw_error,
                )
                http_status = (
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                    if code == 'UNSUPPORTED_BILL'
                    else status.HTTP_400_BAD_REQUEST
                )
                return Response(
                    {'error': _friendly_parse_error(raw_error), 'error_code': code},
                    status=http_status,
                )
            formatted_data_list = context["formatted_data"]
            for formatted_data in formatted_data_list:
                if isinstance(formatted_data, dict):
                    formatted_text = formatted_data.get("formatted") or ''
                    if isinstance(formatted_text, str):
                        formatted_text = formatted_text.rstrip()
                    results.append({
                        "id": formatted_data.get("id"),
                        "uuid": formatted_data.get("uuid") or formatted_data.get("id"),
                        "formatted": formatted_text,
                        "edited_formatted": formatted_data.get("edited_formatted") or formatted_text,
                        "ai_choose": formatted_data.get("selected_expense_key"),
                        "ai_candidates": formatted_data.get("expense_candidates_with_score", []),
                        "counterparty": formatted_data.get("counterparty", ""),
                        "commodity": formatted_data.get("commodity", ""),
                        "payment_method": formatted_data.get("payment_method", ""),
                        "transaction_type": formatted_data.get("transaction_type", ""),
                        "installment_role": formatted_data.get("installment_role"),
                        "installment_period": formatted_data.get("installment_period"),
                        "tag_details": formatted_data.get("tag_details") or [],
                        "original_row": formatted_data.get("original_row") or {},
                    })
                else:
                    results.append({
                        "id": formatted_data.get("id") if hasattr(formatted_data, 'get') else None,
                        "formatted": formatted_data,
                        "edited_formatted": formatted_data,
                        "ai_choose": None,
                        "ai_candidates": [],
                    })
            response_data = {
            "results": results,
            "summary": {"count": len(results)},
            "status": "success",
            "diagnostics": {
                "total_rows": len(context.get("initialized_bill") or []),
                "prefiltered_rows": len(context.get("prefilter_bill") or []),
                "filtered_rows": len(context.get("filtered_data") or []),
            },
            }
            return Response(response_data, status=status.HTTP_200_OK)
        except DecryptionError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UnsupportedFileTypeError as e:
            return Response({'error': str(e)}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(e)
            return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ReparseEntryView(APIView):
    """AI反馈重新解析接口

    此视图处理AI反馈条目的重新解析请求，对指定条目执行新的分析并返回更新后的解析结果。

    Args:
        entry_id (str): 要重新解析的条目ID
        user_selected_key (str): 用户选择的映射关键字

    Returns:
        entry_id (str): 要重新解析的条目ID
        formatted (str): 解析后的条目内容
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ReparseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        entry_id = serializer.validated_data['entry_id']
        selected_key = serializer.validated_data['selected_key']
        mapping_type = serializer.validated_data.get('mapping_type') or 'expense'
        cache_key = entry_id
        cache_data = cache.get(cache_key)

        if not cache_data:
            return Response({'error': '缓存已过期或条目不存在'}, status=status.HTTP_404_NOT_FOUND)

        original_row = cache_data['original_row']
        cached_parsed = cache_data.get('parsed_entry') or {}
        owner_id = get_token_user_id(request)
        user = User.objects.get(id=owner_id)
        config = _apply_parse_engine(
            get_user_config(user), cache_data.get('parse_engine')
        )
        expense_selected_key = selected_key
        if mapping_type == 'asset':
            expense_selected_key = cached_parsed.get('selected_expense_key') or None
            if expense_selected_key == '':
                expense_selected_key = None
        # 重新解析交易记录
        try:
            refund_peer = resolve_refund_peer_for_row(
                original_row, user, owner_id, config, expense_selected_key
            )
            base_parsed = single_parse_transaction(
                original_row, owner_id, config, expense_selected_key, refund_peer=refund_peer
            )
            parsed_entry = resolve_reparse_entry(
                base_parsed,
                original_row,
                installment_role=cached_parsed.get('installment_role'),
                installment_period=cached_parsed.get('installment_period'),
            )

            formatted = FormatData.format_instance(parsed_entry, config=config)

            # 更新缓存
            cache.set(cache_key, {
                "parsed_entry": parsed_entry,
                "original_row": original_row,
                "parse_engine": getattr(config, 'ai_model', None) or 'BERT',
            }, timeout=3600)
            response_ai_choose = (
                cached_parsed.get('selected_expense_key')
                if mapping_type == 'asset'
                else selected_key
            )
            if parsed_entry.get('installment_role') == 'installment':
                response_ai_choose = None
            formatted_text = formatted.rstrip() if formatted else ''
            return Response({
                "id": entry_id,
                "uuid": entry_id,
                "formatted": formatted_text,
                "edited_formatted": formatted_text,
                "ai_choose": response_ai_choose,
                "ai_candidates": parsed_entry.get('expense_candidates_with_score') or [],
                "counterparty": original_row.get("counterparty", ""),
                "commodity": original_row.get("commodity", ""),
                "payment_method": original_row.get("payment_method", ""),
                "transaction_type": original_row.get("transaction_type", ""),
                "installment_role": parsed_entry.get("installment_role"),
                "installment_period": parsed_entry.get("installment_period"),
                "tag_details": parsed_entry.get("tag_details") or [],
                "original_row": original_row,
            }, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception(e)
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ValidateEntryView(APIView):
    """单条 Beancount 语法校验（不阻断保存，仅返回 warning）"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        edited_formatted = request.data.get('edited_formatted')
        if edited_formatted is None:
            return Response(
                {'error': '缺少必要参数：edited_formatted'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from project.apps.translate.utils.beancount_validator import BeancountValidator

        response_data = {'edited_formatted': edited_formatted}
        is_valid, validation_error = BeancountValidator.validate_single_entry(
            edited_formatted or '', check_balance=True
        )
        if not is_valid and validation_error:
            response_data['validation_warning'] = validation_error
        return Response(response_data, status=status.HTTP_200_OK)


class MultiBillAnalyzeView(APIView):
    """多账单解析接口

    该接口实现解析单个/多个文件

    Args:
        files (list): 上传的文件列表

    Returns:
        包含Celery任务组 ID 的响应
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file_ids = request.data.get('file_ids', [])

        # 检查文件是否已在处理中
        pending_files = []
        for file_id in file_ids:
            parse_file = ParseFile.objects.filter(file_id=file_id).first()
            if parse_file and parse_file.status in ['pending', 'processing']:
                pending_files.append(file_id)

        if pending_files:
            return Response({
                'error': '部分文件已在处理队列中',
                'pending_files': pending_files
            }, status=status.HTTP_400_BAD_REQUEST)

        # 获取用户的解析模式偏好
        config = FormatConfig.get_user_config(request.user)
        parsing_mode = config.parsing_mode_preference if hasattr(config, 'parsing_mode_preference') else 'review'
        
        # 更新文件状态为待处理
        for file_id in file_ids:
            parse_file, _ = ParseFile.objects.get_or_create(file_id=file_id)
            parse_file.status = 'pending'
            parse_file.save()

        # 创建任务组
        tasks = []
        # 根据用户偏好设置是否立即写入
        # 审核模式：不立即写入，生成解析待办
        # 直接写入模式：立即写入文件
        args = {
            'write': (parsing_mode == 'direct_write'),
            'cmb_credit_ignore': True,
            'boc_debit_ignore': True,
            'password': request.data.get('password') or None,
        }

        for file_id in file_ids:
            # 如果提供了 passwords 字典，为每个文件分配对应的密码
            passwords = request.data.get('passwords', {})
            file_args = args.copy()
            if str(file_id) in passwords:
                file_args['password'] = passwords[str(file_id)]
                
            task = parse_single_file_task.s(file_id, request.user.id, file_args)
            tasks.append(task)

        task_group = group(tasks)
        group_result = task_group.apply_async()

         # 获取每个任务的任务ID
        task_ids = [task.id for task in group_result.children] if group_result.children else []

        # 生成任务组ID（使用UUID避免冲突）
        task_group_id = str(uuid.uuid4())

        # 存储任务组信息到Redis
        task_group_info = {
            'group_id': group_result.id,
            'created_at': time.time(),
            'file_ids': file_ids,
            'task_ids': task_ids,
            'status': 'pending'
        }
        cache.set(f'task_group:{task_group_id}', json.dumps(task_group_info), timeout=24*3600)

        # 初始化任务状态
        for task_id in task_group_info['task_ids']:
            cache.set(f'task_status:{task_id}', {
                'status': 'pending',
                'file_id': file_ids[task_group_info['task_ids'].index(task_id)]
            }, timeout=24*3600)

        # 如果是审核模式，返回解析待办ID列表
        response_data = {
            'task_group_id': task_group_id,
            'status': 'pending'
        }
        
        if parsing_mode == 'review':
            # 审核模式：返回当前用户全局唯一的条目审核待办ID
            from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
            review_task = EntryReviewQueueService.get_or_create_task(request.user)
            response_data['entry_review_task_id'] = review_task.id

        return Response(response_data, status=status.HTTP_202_ACCEPTED)


class WriteCollectView(APIView):
    """解析结果直接写入账本（追加到 trans/collect.bean）

    供 Web 解析页在解析、核对后一键入账：先做 Beancount 语法校验，
    全部通过后按追加语义写入当前用户的 ``trans/collect.bean``
    （文件与 include 缺失时自动补齐），不做条目去重、不产生审核待办。
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """写入解析条目

        POST /api/translate/write-collect
        请求体：``{"entries": [{"uuid": "...", "directive": "..."}]}``
        """
        from project.apps.translate.utils.beancount_validator import BeancountValidator
        from project.utils.file import BeanFileManager

        raw_entries = request.data.get('entries')
        if not isinstance(raw_entries, list):
            return Response({'error': '缺少必要参数：entries'}, status=status.HTTP_400_BAD_REQUEST)

        # 保留 uuid 与指令的对应关系，便于定位错误条目
        queued = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            directive = entry.get('directive')
            if not isinstance(directive, str) or not directive.strip():
                continue
            queued.append({
                'uuid': str(entry.get('uuid') or ''),
                'directive': directive.rstrip(),
            })

        if not queued:
            return Response({'error': '没有可写入的条目'}, status=status.HTTP_400_BAD_REQUEST)

        directives = [item['directive'] for item in queued]
        try:
            is_valid, error_message, _ = BeancountValidator.validate_entries(
                '\n\n'.join(directives)
            )
            if not is_valid:
                _, _, error_entries_indices = BeancountValidator.validate_multiple_entries(directives)
                if not error_entries_indices:
                    # 单条校验无法定位时，整体报错并指向首条
                    error_entries_indices = [(0, error_message or '格式有误')]
                return Response(
                    {
                        'error': f'Beancount 语法错误: 共 {len(error_entries_indices)} 条格式有误',
                        'error_entries': [
                            {
                                'uuid': queued[idx]['uuid'],
                                'index': idx,
                                'error_message': msg or error_message or '格式有误',
                            }
                            for idx, msg in error_entries_indices
                        ],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            BeanFileManager.append_to_collect_bean(request.user, directives)

            # 写入后维护对账条目：为被后补交易打破的 balance 断言补 pad，
            # 并注释因真实交易落入断言容差而变为无用的 pad（best-effort）
            from project.apps.reconciliation.services.reconciliation_comment_service import (
                ReconciliationCommentService,
            )
            ReconciliationCommentService.maintain_ledger(request.user)
        except Exception as e:
            logger.exception(e)
            return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'message': '写入成功',
            'entry_count': len(directives),
            'bean': 'trans/collect.bean',
        }, status=status.HTTP_200_OK)


class UploadParseReviewView(APIView):
    """上传账单直接解析并生成解析审核待办

    与 Web 解析首页（`/translate/trans`）一样同步解析、不落文件管理：
    不创建 File / ParseFile、不生成账单 ``.bean``、也不改动 main.bean 的 include。

    落库策略固定为「审核模式」，**不随** ``FormatConfig.parsing_mode_preference``
    变化：上传解析一律进入用户级审核队列并激活 entry_review 待办，确认写入或
    到期自动写入时追加到 ``trans/collect.bean``。需要「解析后直接入账」请走
    Web 解析页的「写入账本」（``/translate/write-collect``）。
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """上传解析并入队

        POST /api/translate/upload-parse（multipart，文件字段 ``trans``）
        """
        from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
        from project.apps.translate.services.entry_dedup_service import EntryDedupService
        from project.apps.translate.services.copilot_bookkeeping_service import CopilotBookkeepingService

        serializer = AnalyzeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = request.FILES.get('trans', None)
        if not uploaded_file:
            return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        config = get_user_config(user)
        # 审核模式：只解析入队，不写任何账本文件
        args = {**serializer.validated_data, 'write': False}

        try:
            context = AnalyzeService(user=user, config=config).analyze_single_file(
                uploaded_file, args
            )
            formatted_data = context.get('formatted_data') or []
            entries = self._build_review_entries(formatted_data, uploaded_file.name)
            if not entries:
                return Response(
                    {'error': '未解析到有效条目'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            kept, duplicates = entries, []
            acquired = EntryReviewQueueService.acquire_lock(user.id)
            try:
                if acquired:
                    existing_entries = EntryReviewQueueService.list_entries(user.id)
                    kept, duplicates = EntryDedupService.dedup_new_entries(
                        user, entries, existing_entries
                    )
                else:
                    # 未拿到锁：跳过去重，全部入队，避免条目丢失
                    logger.warning(
                        '未获取到条目审核队列锁，跳过去重: user_id=%s', user.id
                    )

                if kept:
                    CopilotBookkeepingService.append_bill_entries(user, kept)
            finally:
                if acquired:
                    EntryReviewQueueService.release_lock(user.id)

            task = EntryReviewQueueService.get_or_create_task(user)
            return Response({
                'status': 'success',
                'file_name': uploaded_file.name,
                'entry_count': len(kept),
                'duplicate_count': len(duplicates),
                'pending_total': len(EntryReviewQueueService.list_entries(user.id)),
                'entry_review_task_id': task.id,
            }, status=status.HTTP_200_OK)
        except DecryptionError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except UnsupportedFileTypeError as e:
            return Response({'error': str(e)}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(e)
            return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @staticmethod
    def _build_review_entries(formatted_data, file_name):
        """把解析管道的格式化结果转成审核条目（uuid 取逐条 cache_key）。

        与 ``tasks.parse_single_file_task`` 审核模式的构造保持一致，
        并额外写入 ``file_name``，供审核页与移动端按上传账单名分组展示。
        """
        from project.apps.translate.services.parse_review_service import ParseReviewService

        entries = []
        for entry in formatted_data:
            if not isinstance(entry, dict):
                continue
            # FormatStep 输出的 id 即 CacheStep 写入的 cache_key
            cache_key = entry.get('id') or entry.get('uuid')
            if not cache_key:
                continue

            cache_entry_data = cache.get(cache_key)
            cached_parsed = {}
            cached_original_row = None
            if isinstance(cache_entry_data, dict):
                cached_original_row = cache_entry_data.get('original_row')
                cached_parsed = cache_entry_data.get('parsed_entry') or {}

            formatted_text = entry.get('formatted') or ''
            entries.append({
                'uuid': cache_key,
                'formatted': formatted_text,
                'edited_formatted': formatted_text,
                'selected_expense_key': entry.get('selected_expense_key', ''),
                'expense_candidates_with_score': entry.get('expense_candidates_with_score', []),
                'original_row': cached_original_row or entry.get('original_row') or {},
                'tag_details': cached_parsed.get('tag_details') or entry.get('tag_details') or [],
                'tag_overrides': ParseReviewService.default_tag_overrides(),
                'installment_role': (
                    entry.get('installment_role') or cached_parsed.get('installment_role')
                ),
                'installment_period': (
                    entry.get('installment_period')
                    if entry.get('installment_period') is not None
                    else cached_parsed.get('installment_period')
                ),
                'file_name': file_name,
            })
        return entries


class TaskGroupStatusView(APIView):
    """任务组状态查询接口
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        task_group_id = request.query_params.get('task_group_id')
        # task_group_id = request.data.get('task_group_id')
        if not task_group_id:
            return Response({'error': '缺少任务组ID'}, status=status.HTTP_400_BAD_REQUEST)

        # 从缓存获取任务组信息
        task_group_info = cache.get(f'task_group:{task_group_id}')
        if not task_group_info:
            return Response({'error': '任务组不存在或已过期'}, status=status.HTTP_404_NOT_FOUND)

        task_group_info = json.loads(task_group_info)
        group_result = GroupResult.restore(task_group_info['group_id'])

        # 获取所有任务状态
        tasks_status = []
        completed_count = 0

        for task_id in task_group_info['task_ids']:
            task_status = cache.get(f'task_status:{task_id}') or {'status': 'unknown'}
            tasks_status.append({
                'task_id': task_id,
                'file_id': task_status.get('file_id'),
                'status': task_status['status'],
                'error': task_status.get('error')
            })
            # pending_review 表示解析已完成，只是需要审核，应该计入已完成
            if task_status['status'] in ['parsed', 'failed', 'cancelled', 'pending_review']:
                completed_count += 1

        # 检查整体状态
        if completed_count == len(tasks_status):
            # 所有子任务都已完成（无论成功/失败）
            task_group_info['status'] = 'completed'
        elif group_result and group_result.ready():
            task_group_info['status'] = 'completed'
        else:
            task_group_info['status'] = 'processing'

        # 更新缓存
        cache.set(f'task_group:{task_group_id}', json.dumps(task_group_info), timeout=24*3600)

        return Response({
            'task_group_id': task_group_id,
            'status': task_group_info['status'],
            'progress': f'{completed_count}/{len(tasks_status)}',
            'tasks': tasks_status
        })


class CancelParseView(APIView):
    """取消解析接口

    取消指定文件的解析任务
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file_ids = request.data.get('file_ids', [])
        if not file_ids:
            return Response({'error': '缺少文件ID列表'}, status=status.HTTP_400_BAD_REQUEST)

        from project.apps.file_manager.models import File
        from project.utils.file import BeanFileManager
        from project.apps.translate.services.entry_review_queue_service import (
            EntryReviewQueueService,
        )

        cancelled_files = []
        for file_id in file_ids:
            try:
                # 验证文件是否属于当前用户
                file_obj = File.objects.get(id=file_id, owner=request.user)
                
                # 获取或创建 ParseFile 对象
                parse_file, _ = ParseFile.objects.get_or_create(file_id=file_id)
                
                # 检查状态是否可以取消（pending/processing/parsed都可以取消）
                # parsed 状态取消时清除 .bean 文件内容
                if parse_file.status not in ['pending', 'processing', 'parsed']:
                    continue  # 跳过不能取消的状态
                
                # 更新状态为 cancelled
                parse_file.status = 'cancelled'
                parse_file.save()
                
                # 清空对应的 .bean 文件内容
                BeanFileManager.clear_bean_file(request.user, file_obj.get_bean_relative_path())
                
                # 从用户级统一审核队列中移除该文件的所有引用
                # 队列为空时把条目审核待办置为未激活，便于用户重新解析文件
                EntryReviewQueueService.remove_file(request.user.id, file_id)
                EntryReviewQueueService.deactivate_if_empty(request.user)

                cancelled_files.append(file_id)
                
                # 注意：这里无法直接撤销已提交到 Celery 队列的任务
                # 任务执行时会检查 ParseFile.status，如果已经是 cancelled 会直接返回
                # 这样可以实现软取消：标记状态为 cancelled，任务开始执行时发现已取消就不处理
                        
            except File.DoesNotExist:
                logger.warning(f"文件不存在或不属于当前用户: file_id={file_id}, user={request.user.username}")
                continue
            except Exception as e:
                logger.error(f"取消解析失败: file_id={file_id}, error={str(e)}")
                continue

        if not cancelled_files:
            return Response({'error': '没有可取消的文件'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'message': f'成功处理 {len(cancelled_files)} 个文件（已取消解析并清除解析结果）',
            'cancelled_files': cancelled_files
        }, status=status.HTTP_200_OK)


class EntryReviewViewSet(APIView):
    """统一条目审核视图基类

    每个用户全局唯一一个条目审核待办，所有审核接口以 file_id 定位具体文件，
    待审核条目集合由 EntryReviewQueueService 管理。
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    SOURCE_FILE = 'file'
    SOURCE_COPILOT = 'copilot'
    SOURCE_RECURRING = 'recurring'
    VALID_SOURCES = (SOURCE_FILE, SOURCE_COPILOT, SOURCE_RECURRING)

    def get_review_task(self, request):
        """返回当前用户的统一条目审核待办（可能为 None）"""
        from django.contrib.auth import get_user_model
        content_type = ContentType.objects.get_for_model(get_user_model())
        return ScheduledTask.objects.filter(
            task_type='entry_review',
            content_type=content_type,
            object_id=request.user.id,
        ).first()

    def get_parse_file(self, request, file_id):
        """校验 file_id 归属当前用户；返回 (parse_file, error_response)"""
        parse_file = ParseFile.objects.filter(file_id=file_id).select_related('file').first()
        if parse_file is None:
            return None, Response({'error': '文件不存在'}, status=status.HTTP_404_NOT_FOUND)
        if parse_file.file.owner != request.user:
            return None, Response({'error': '无权访问该文件'}, status=status.HTTP_403_FORBIDDEN)
        return parse_file, None

    def ensure_editable(self, request, parse_file):
        """校验：待办处于 pending、文件处于 pending_review、未过期；返回 error_response 或 None"""
        from project.apps.translate.services.parse_review_service import ParseReviewService
        task = self.get_review_task(request)
        if task is None or task.status != 'pending':
            return Response({'error': '该待办已完成或已取消'}, status=status.HTTP_400_BAD_REQUEST)
        if parse_file.status != 'pending_review':
            return Response({'error': '该文件当前不可审核'}, status=status.HTTP_400_BAD_REQUEST)
        cached_data = ParseReviewService.get_parse_result(parse_file.file_id)
        if ParseReviewService.is_review_expired(cached_data, None):
            return Response({'error': '解析待办已过期，系统将自动写入'}, status=status.HTTP_400_BAD_REQUEST)
        return None

    def get_source(self, request, data):
        """解析请求体中的 source；缺省与非法值均按账单文件来源处理。"""
        raw_source = (data or {}).get('source')
        source = str(raw_source or self.SOURCE_FILE).strip().lower()
        if source not in self.VALID_SOURCES:
            return self.SOURCE_FILE
        return source

    def ensure_copilot_editable(self, request):
        """校验 Copilot 暂存区可审核：待办 pending、暂存区存在且未过期；返回 error_response 或 None"""
        from project.apps.translate.services.parse_review_service import ParseReviewService
        from project.apps.translate.services.copilot_bookkeeping_service import (
            CopilotBookkeepingService,
        )

        task = self.get_review_task(request)
        if task is None or task.status != 'pending':
            return Response({'error': '该待办已完成或已取消'}, status=status.HTTP_400_BAD_REQUEST)
        cached_data = CopilotBookkeepingService.get_staging_data(request.user.id)
        if not cached_data:
            return Response(
                {'error': 'Copilot 记账待审核条目不存在或已过期'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if ParseReviewService.is_review_expired(cached_data, None):
            return Response(
                {'error': '解析待办已过期，系统将自动写入'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def ensure_recurring_editable(self, request):
        """校验循环待办暂存区可审核：待办 pending、暂存区存在；返回 error_response 或 None"""
        from project.apps.translate.services.recurring_bookkeeping_service import (
            RecurringBookkeepingService,
        )

        task = self.get_review_task(request)
        if task is None or task.status != 'pending':
            return Response({'error': '该待办已完成或已取消'}, status=status.HTTP_400_BAD_REQUEST)
        if not RecurringBookkeepingService.has_staging(request.user.id):
            return Response(
                {'error': '循环待办待审核条目不存在或已失效'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def resolve_source(self, request, data):
        """解析来源并完成对应校验，返回 (source, file_id, error_response)。

        - file：沿用 get_parse_file + ensure_editable（含 ParseFile 状态校验）
        - copilot：不查 ParseFile，仅校验待办 / 暂存区 / 是否过期，file_id 恒为 None
        - recurring：不查 ParseFile，仅校验待办 / 循环暂存区，file_id 恒为 None
        """
        source = self.get_source(request, data)
        if source == self.SOURCE_COPILOT:
            return source, None, self.ensure_copilot_editable(request)
        if source == self.SOURCE_RECURRING:
            return source, None, self.ensure_recurring_editable(request)

        file_id = (data or {}).get('file_id')
        parse_file, error_response = self.get_parse_file(request, file_id)
        if error_response:
            return source, file_id, error_response
        editable_error = self.ensure_editable(request, parse_file)
        if editable_error:
            return source, file_id, editable_error
        return source, file_id, None

    def source_cache_key(self, source, user, file_id):
        """来源对应的 ParseReviewService 缓存键参数。"""
        if source == self.SOURCE_COPILOT:
            from project.apps.translate.services.copilot_bookkeeping_service import (
                CopilotBookkeepingService,
            )

            return CopilotBookkeepingService.staging_key(user.id)
        if source == self.SOURCE_RECURRING:
            from project.apps.translate.services.recurring_bookkeeping_service import (
                RecurringBookkeepingService,
            )

            return RecurringBookkeepingService.staging_key(user.id)
        return file_id


def _serialize_parse_review_entry(
    entry_uuid: str,
    updated_entry: Optional[dict],
    formatted: str,
    selected_key: Optional[str],
    parsed_entry: dict,
) -> dict:
    """构造单条解析审核重解析 API 响应字段。"""
    from project.apps.translate.services.parse_review_service import ParseReviewService

    formatted_result = updated_entry.get('formatted') if updated_entry else formatted
    edited_formatted_result = (
        updated_entry.get('edited_formatted') if updated_entry else formatted
    )
    formatted_result = formatted_result.rstrip() if formatted_result else ''
    edited_formatted_result = edited_formatted_result.rstrip() if edited_formatted_result else ''
    is_installment = parsed_entry.get('installment_role') == 'installment'
    tag_payload = (
        ParseReviewService.entry_response_payload(updated_entry)
        if updated_entry
        else {
            'tag_details': ParseReviewService.apply_tag_overrides(
                parsed_entry.get('tag_details', []),
                ParseReviewService.default_tag_overrides(),
            ),
            'tag_overrides': ParseReviewService.default_tag_overrides(),
        }
    )
    return {
        'uuid': entry_uuid,
        'formatted': formatted_result,
        'edited_formatted': edited_formatted_result,
        'selected_expense_key': None if is_installment else selected_key,
        'expense_candidates_with_score': parsed_entry.get('expense_candidates_with_score', []),
        **tag_payload,
    }


def _reparse_review_entry(
    *,
    file_id: int,
    entry: dict,
    owner_id: int,
    config,
    user,
    selected_key: Optional[str] = None,
    mapping_type: str = 'expense',
) -> Optional[dict]:
    """重解析单条审核条目并写回 Redis，返回 API 响应字段。"""
    from project.apps.translate.services.parse_review_service import ParseReviewService

    entry_uuid = entry.get('uuid')
    original_row = entry.get('original_row')
    if not entry_uuid or not original_row:
        return None

    expense_selected_key = selected_key
    if mapping_type == 'asset':
        expense_selected_key = entry.get('selected_expense_key') or None
        if expense_selected_key == '':
            expense_selected_key = None

    refund_peer = resolve_refund_peer_for_row(
        original_row, user, owner_id, config, expense_selected_key
    )
    base_parsed = single_parse_transaction(
        original_row, owner_id, config, expense_selected_key, refund_peer=refund_peer
    )
    base_parsed['_original_row'] = original_row
    expanded = expand_parsed_entry(base_parsed, set())
    parsed_entry = pick_reparse_slice(
        expanded,
        entry.get('installment_role'),
        entry.get('installment_period'),
    )
    formatted = FormatData.format_instance(parsed_entry, config=config)
    is_installment = parsed_entry.get('installment_role') == 'installment'
    if mapping_type == 'asset':
        stored_expense_key = parsed_entry.get('selected_expense_key')
    else:
        stored_expense_key = expense_selected_key
    ParseReviewService.update_entry_formatted(
        file_id,
        entry_uuid,
        formatted,
        tag_details=parsed_entry.get('tag_details', []),
        selected_expense_key=None if is_installment else stored_expense_key,
        expense_candidates_with_score=parsed_entry.get(
            'expense_candidates_with_score', []
        ),
    )

    if parsed_entry.get('installment_role') == 'purchase' and len(expanded) > 1:
        cached_after = ParseReviewService.get_parse_result_migrated(file_id) or {}
        for sibling in ParseReviewService.iter_same_order_installment_entries(
            cached_after.get('formatted_data') or [],
            original_row,
            entry_uuid,
        ):
            sib_entry = pick_reparse_slice(
                expanded,
                'installment',
                sibling.get('installment_period'),
            )
            sib_formatted = FormatData.format_instance(sib_entry, config=config)
            ParseReviewService.update_entry_formatted(
                file_id,
                sibling.get('uuid'),
                sib_formatted,
                tag_details=sib_entry.get('tag_details', []),
                selected_expense_key=None,
                expense_candidates_with_score=[],
            )

    updated_result = ParseReviewService.get_parse_result(file_id)
    updated_entry = None
    if updated_result:
        for cached_entry in updated_result.get('formatted_data', []):
            if cached_entry.get('uuid') == entry_uuid:
                updated_entry = cached_entry
                break

    response_selected_key = (
        parsed_entry.get('selected_expense_key')
        if mapping_type == 'asset'
        else expense_selected_key
    )
    return _serialize_parse_review_entry(
        entry_uuid,
        updated_entry,
        formatted,
        response_selected_key,
        parsed_entry,
    )


def _propagate_candidate_key_to_batch(
    *,
    file_id: int,
    mapping_key: str,
    mapping_type: str,
    exclude_uuid: str,
) -> list:
    """将新增的映射关键字补充为同批匹配条目的候选分类（不改动当前分类/条目文本）。"""
    from project.apps.translate.services.parse_review_service import ParseReviewService

    cached = ParseReviewService.get_parse_result_migrated(file_id)
    if not cached:
        return []

    updated_entries = []
    changed = False
    for entry in cached.get('formatted_data') or []:
        entry_uuid = entry.get('uuid')
        if not entry_uuid or entry_uuid == exclude_uuid:
            continue
        if entry.get('installment_role') == 'installment':
            continue
        if not ParseReviewService.row_matches_mapping_key(
            entry.get('original_row'), mapping_key, mapping_type=mapping_type
        ):
            continue
        candidates = entry.get('expense_candidates_with_score') or []
        if any(str(item.get('key')) == mapping_key for item in candidates):
            continue
        candidates = [*candidates, {'key': mapping_key, 'score': 1.0}]
        entry['expense_candidates_with_score'] = candidates
        changed = True
        updated_entries.append({
            'uuid': entry_uuid,
            'file_id': file_id,
            'expense_candidates_with_score': candidates,
        })

    if changed:
        ParseReviewService.save_parse_result(
            file_id,
            cached,
            timeout=ParseReviewService._ttl_for_resave(file_id),
        )
    return updated_entries


class EntryReviewResultsView(EntryReviewViewSet):
    """获取用户级统一审核结果"""

    def get(self, request):
        """获取待审核条目列表

        GET /api/translate/entry-review/results
        """
        from project.apps.translate.services.parse_review_service import ParseReviewService
        from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService

        config = get_user_config(request.user)

        # 收集队列涉及的账单文件 file_id（去重）；Copilot 暂存区不做 tag 回填
        file_ids = []
        seen = set()
        for ref in EntryReviewQueueService.list_refs(request.user.id):
            if EntryReviewQueueService.ref_source(ref) != EntryReviewQueueService.SOURCE_FILE:
                continue
            file_id = ref.get('file_id')
            if file_id is None or file_id in seen:
                continue
            seen.add(file_id)
            file_ids.append(file_id)

        # 逐文件回填 tag_details，并做 uuid 迁移
        for file_id in file_ids:
            data = ParseReviewService.get_parse_result_migrated(file_id)
            if data is None:
                continue
            if ParseReviewService.backfill_tag_details_in_data(
                data, request.user.id, config, user=request.user
            ):
                ParseReviewService.save_parse_result(
                    file_id, data, timeout=ParseReviewService._ttl_for_resave(file_id)
                )

        # 条目副本，可安全修改
        entries = EntryReviewQueueService.list_entries(request.user.id)
        for entry in entries:
            ParseReviewService.normalize_entry_tag_fields(entry)
            if 'formatted' in entry:
                entry['formatted'] = entry['formatted'].rstrip() if entry['formatted'] else ''
            if 'edited_formatted' in entry:
                entry['edited_formatted'] = entry['edited_formatted'].rstrip() if entry['edited_formatted'] else ''
            entry['tag_details'] = ParseReviewService.get_effective_tag_details(entry)

        # 标记需要人工审核的条目：兜底账户或结构异常。其余为「自动通过」，仍全量返回。
        from project.apps.translate.services.entry_review_attention import annotate_entries

        entries = annotate_entries(
            entries,
            getattr(config, 'reconciliation_fallback_account', None),
        )
        attention_count = sum(1 for entry in entries if entry.get('needs_review'))

        return Response({
            'entries': entries,
            'entry_count': len(entries),
            'attention_entry_count': attention_count,
            'auto_passed_entry_count': len(entries) - attention_count,
            'review_expires_at': EntryReviewQueueService.earliest_expires_at(request.user.id),
        }, status=status.HTTP_200_OK)


class EntryReviewReparseView(EntryReviewViewSet):
    """重解析单个条目"""

    def post(self, request):
        """重解析单个条目

        POST /api/translate/entry-review/reparse
        Body: {"file_id": ..., "entry_uuid": "...", "selected_key": "...", "mapping_type": "expense"}
        """
        source = self.get_source(request, request.data)
        if source == self.SOURCE_COPILOT:
            return Response(
                {'error': 'Copilot 记账条目不支持重新解析，请直接编辑条目文本'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if source == self.SOURCE_RECURRING:
            return Response(
                {'error': '循环待办条目不支持重新解析，请直接编辑条目文本'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_id = request.data.get('file_id')
        parse_file, error_response = self.get_parse_file(request, file_id)
        if error_response:
            return error_response

        editable_error = self.ensure_editable(request, parse_file)
        if editable_error:
            return editable_error

        entry_uuid = request.data.get('entry_uuid')
        selected_key = request.data.get('selected_key')
        mapping_type = request.data.get('mapping_type') or 'expense'
        if mapping_type not in ('expense', 'income', 'asset'):
            mapping_type = 'expense'
        # 新增/编辑映射时，把新关键字补充为同批匹配条目的候选分类（可选）
        propagate_candidates = bool(request.data.get('propagate_candidates'))

        if not entry_uuid or not selected_key:
            return Response(
                {'error': '缺少必要参数：entry_uuid 和 selected_key'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 从缓存获取解析结果
        from project.apps.translate.services.parse_review_service import ParseReviewService
        parse_result = ParseReviewService.get_parse_result_migrated(file_id)

        if parse_result is None:
            return Response(
                {'error': '解析结果不存在或已过期'},
                status=status.HTTP_404_NOT_FOUND
            )

        # 查找对应的条目
        formatted_data = parse_result.get('formatted_data', [])
        target_entry = None
        for entry in formatted_data:
            if entry.get('uuid') == entry_uuid:
                target_entry = entry
                break

        if not target_entry:
            return Response(
                {'error': '未找到对应的条目'},
                status=status.HTTP_404_NOT_FOUND
            )

        # 获取原始数据
        original_row = target_entry.get('original_row')
        if not original_row:
            return Response(
                {'error': '条目缺少原始数据，无法重解析'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 执行重解析
        try:
            owner_id = request.user.id
            config = get_user_config(request.user)
            payload = _reparse_review_entry(
                file_id=file_id,
                entry=target_entry,
                owner_id=owner_id,
                config=config,
                user=request.user,
                selected_key=selected_key,
                mapping_type=mapping_type,
            )
            if payload is None:
                return Response(
                    {'error': '重解析失败'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            response_data = payload
            if propagate_candidates and mapping_type != 'asset':
                from project.apps.translate.services.entry_review_queue_service import (
                    EntryReviewQueueService,
                )

                # 覆盖解析审核页当前全部账单（同批 + 跨账单）
                target_file_ids = [parse_file.file_id]
                for ref in EntryReviewQueueService.list_refs(request.user.id):
                    if (
                        EntryReviewQueueService.ref_source(ref)
                        != EntryReviewQueueService.SOURCE_FILE
                    ):
                        continue
                    ref_file_id = ref.get('file_id')
                    if ref_file_id is None or ref_file_id in target_file_ids:
                        continue
                    target_file_ids.append(ref_file_id)

                candidate_updated_entries = []
                for target_file_id in target_file_ids:
                    candidate_updated_entries.extend(
                        _propagate_candidate_key_to_batch(
                            file_id=target_file_id,
                            mapping_key=selected_key,
                            mapping_type=mapping_type,
                            exclude_uuid=entry_uuid,
                        )
                    )

                response_data = {
                    **payload,
                    'candidate_updated_entries': candidate_updated_entries,
                }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(e)
            return Response(
                {'error': f'重解析失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class EntryReviewEditView(EntryReviewViewSet):
    """更新编辑内容"""

    def put(self, request, uuid):
        """更新编辑内容

        PUT /api/translate/entry-review/entries/{uuid}/edit
        Body: {"file_id": ..., "edited_formatted": "...", "source": "file|copilot|recurring"}
        """
        source, file_id, error_response = self.resolve_source(request, request.data)
        if error_response:
            return error_response

        edited_formatted = request.data.get('edited_formatted')
        if edited_formatted is None:
            return Response(
                {'error': '缺少必要参数：edited_formatted'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 更新缓存（先迁移 uuid，再解析路径中的占位符）
        from project.apps.translate.services.parse_review_service import ParseReviewService
        from project.apps.translate.utils.beancount_validator import BeancountValidator

        cache_key = self.source_cache_key(source, request.user, file_id)

        migrated = ParseReviewService.get_parse_result_migrated(cache_key)
        if migrated is None:
            return Response(
                {'error': '解析结果不存在或已过期，请重新解析'},
                status=status.HTTP_404_NOT_FOUND,
            )

        entry_uuid = uuid
        if uuid in ('null', 'undefined', 'None'):
            fd = migrated.get('formatted_data') or []
            if len(fd) == 1 and fd[0].get('uuid'):
                entry_uuid = fd[0]['uuid']

        success = ParseReviewService.update_entry_edited_formatted(
            cache_key, entry_uuid, edited_formatted
        )

        if not success:
            return Response(
                {'error': '更新编辑内容失败'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # 返回更新后的结果
        updated_result = ParseReviewService.get_parse_result(cache_key)
        updated_entry = None
        for entry in updated_result.get('formatted_data', []):
            if entry.get('uuid') == entry_uuid:
                updated_entry = entry
                break

        content_to_validate = updated_entry.get('edited_formatted') if updated_entry else edited_formatted
        response_data = {
            'uuid': entry_uuid,
            'edited_formatted': content_to_validate,
            'source': source,
            'file_id': file_id,
        }
        # 保存后对单条内容做校验，作为即时反馈（不阻断保存）
        is_valid, validation_error = BeancountValidator.validate_single_entry(
            content_to_validate or '', check_balance=True
        )
        if not is_valid and validation_error:
            response_data['validation_warning'] = validation_error

        return Response(response_data, status=status.HTTP_200_OK)


class EntryReviewTagsView(EntryReviewViewSet):
    """更新条目标签（添加/移除）"""

    def patch(self, request, uuid):
        """PATCH /api/translate/entry-review/entries/{uuid}/tags

        Body: {"file_id": ..., "action": "add|remove", "tag_path": "Category/EDUCATION", "source": "file|copilot|recurring"}
        """
        source, file_id, error_response = self.resolve_source(request, request.data)
        if error_response:
            return error_response

        action = request.data.get('action')
        tag_path = request.data.get('tag_path')
        if action not in ('add', 'remove') or not tag_path:
            return Response(
                {'error': '缺少必要参数：action（add/remove）与 tag_path'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from project.apps.translate.services.parse_review_service import ParseReviewService

        cache_key = self.source_cache_key(source, request.user, file_id)

        migrated = ParseReviewService.get_parse_result_migrated(cache_key)
        if migrated is None:
            return Response(
                {'error': '解析结果不存在或已过期，请重新解析'},
                status=status.HTTP_404_NOT_FOUND,
            )

        entry_uuid = uuid
        if uuid in ('null', 'undefined', 'None'):
            fd = migrated.get('formatted_data') or []
            if len(fd) == 1 and fd[0].get('uuid'):
                entry_uuid = fd[0]['uuid']

        result = ParseReviewService.update_entry_tags(
            cache_key,
            entry_uuid,
            action,
            tag_path,
        )
        if result is None:
            return Response(
                {'error': '更新标签失败或未找到条目'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result['source'] = source
        result['file_id'] = file_id
        return Response(result, status=status.HTTP_200_OK)


class EntryReviewPreviewSyncView(EntryReviewViewSet):
    """预览批量同步（以预览文本为真源，支持删条）"""

    def put(self, request):
        """PUT /api/translate/entry-review/preview-sync

        Body: {"file_id": ..., "entries": [{"uuid": "...", "edited_formatted": "..."}], "source": "file|copilot|recurring"}
        """
        source, file_id, error_response = self.resolve_source(request, request.data)
        if error_response:
            return error_response

        entries = request.data.get('entries')
        if not isinstance(entries, list):
            return Response(
                {'error': '缺少必要参数：entries'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from project.apps.translate.services.parse_review_service import ParseReviewService
        from project.apps.translate.utils.beancount_validator import BeancountValidator

        cache_key = self.source_cache_key(source, request.user, file_id)

        migrated = ParseReviewService.get_parse_result_migrated(cache_key)
        if migrated is None:
            return Response(
                {'error': '解析结果不存在或已过期，请重新解析'},
                status=status.HTTP_404_NOT_FOUND,
            )

        sync_result = ParseReviewService.sync_entries_from_preview(
            cache_key,
            entries,
        )
        if sync_result is None:
            return Response(
                {'error': '预览同步失败，请检查条目 uuid 是否有效'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        formatted_data = sync_result['formatted_data']
        for entry in formatted_data:
            if 'formatted' in entry and entry['formatted']:
                entry['formatted'] = entry['formatted'].rstrip()
            if 'edited_formatted' in entry and entry['edited_formatted']:
                entry['edited_formatted'] = entry['edited_formatted'].rstrip()

        validation_warnings: Dict[str, str] = {}
        for entry in formatted_data:
            content = entry.get('edited_formatted') or ''
            is_valid, validation_error = BeancountValidator.validate_single_entry(
                content, check_balance=True
            )
            if not is_valid and validation_error:
                entry_uuid = entry.get('uuid')
                if entry_uuid:
                    validation_warnings[entry_uuid] = validation_error

        return Response(
            {
                'formatted_data': formatted_data,
                'removed_count': sync_result['removed_count'],
                'validation_warnings': validation_warnings,
                'source': source,
                'file_id': file_id,
            },
            status=status.HTTP_200_OK,
        )


class EntryReviewConfirmView(EntryReviewViewSet):
    """确认写入（用户级统一审核）"""

    def post(self, request):
        """确认写入

        POST /api/translate/entry-review/confirm
        """
        from project.apps.translate.services.parse_review_service import ParseReviewService
        from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
        from project.apps.translate.services.copilot_bookkeeping_service import (
            CopilotBookkeepingService,
        )
        from project.apps.translate.services.recurring_bookkeeping_service import (
            RecurringBookkeepingService,
        )
        from project.apps.translate.utils.beancount_validator import BeancountValidator
        from project.utils.file import BeanFileManager

        task = self.get_review_task(request)
        if task is None or task.status != 'pending':
            return Response(
                {'error': '该待办已完成或已取消'},
                status=status.HTTP_400_BAD_REQUEST
            )

        refs = EntryReviewQueueService.list_refs(request.user.id)
        if not refs:
            return Response(
                {'error': '待审核队列为空'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 按 (source, file_id) 分组并保持顺序
        groups = []
        group_index = {}
        for ref in refs:
            source = EntryReviewQueueService.ref_source(ref)
            file_id = ref.get('file_id') if source == EntryReviewQueueService.SOURCE_FILE else None
            group_key = (source, file_id)
            if group_key not in group_index:
                group_index[group_key] = len(groups)
                groups.append({'source': source, 'file_id': file_id})

        # 先校验全部来源，全部通过后再写入
        validations = []  # 待写入项：[{source, file_id, parse_file|directives, text, entry_count}]
        error_entries = []  # 结构化错误条目
        pending_removal = []  # 无有效条目、待清理引用的 (source, file_id)
        copilot_key = CopilotBookkeepingService.staging_key(request.user.id)
        for group in groups:
            source = group['source']
            file_id = group['file_id']

            if source == EntryReviewQueueService.SOURCE_COPILOT:
                cached = CopilotBookkeepingService.get_staging_data(request.user.id)
                if not cached:
                    # 暂存区已失效：跳过并记录待清理
                    pending_removal.append((source, file_id))
                    continue

                final_entries = ParseReviewService.get_final_result(copilot_key)
                if not final_entries:
                    pending_removal.append((source, file_id))
                    continue

                directives = [entry['formatted'].rstrip() for entry in final_entries]
                formatted_text = '\n\n'.join(directives)

                is_valid, error_message, _ = BeancountValidator.validate_entries(
                    formatted_text, check_balance=True
                )
                if not is_valid:
                    _, _, error_entries_indices = BeancountValidator.validate_multiple_entries(
                        directives, check_balance=True
                    )
                    if not error_entries_indices:
                        # 整体校验失败但无法逐条定位时，指向首条，避免漏拦后继续写入
                        error_entries_indices = [(0, error_message or '校验未通过')]
                    error_entries.extend([
                        {
                            'source': source,
                            'file_id': None,
                            'uuid': final_entries[idx]['uuid'],
                            'index': idx,
                            'error_message': msg or error_message,
                        }
                        for idx, msg in error_entries_indices
                    ])
                    continue

                validations.append({
                    'source': source,
                    'file_id': None,
                    'directives': directives,
                    'text': formatted_text,
                    'entry_count': len(final_entries),
                })
                continue

            if source == EntryReviewQueueService.SOURCE_RECURRING:
                cached = RecurringBookkeepingService.get_staging_data(request.user.id)
                if not cached:
                    # 暂存区已失效：跳过并记录待清理
                    pending_removal.append((source, file_id))
                    continue

                recurring_key = RecurringBookkeepingService.staging_key(request.user.id)
                final_entries = ParseReviewService.get_final_result(recurring_key)
                if not final_entries:
                    pending_removal.append((source, file_id))
                    continue

                directives = [entry['formatted'].rstrip() for entry in final_entries]
                formatted_text = '\n\n'.join(directives)

                is_valid, error_message, _ = BeancountValidator.validate_entries(
                    formatted_text, check_balance=True
                )
                if not is_valid:
                    _, _, error_entries_indices = BeancountValidator.validate_multiple_entries(
                        directives, check_balance=True
                    )
                    if not error_entries_indices:
                        # 整体校验失败但无法逐条定位时，指向首条，避免漏拦后继续写入
                        error_entries_indices = [(0, error_message or '校验未通过')]
                    error_entries.extend([
                        {
                            'source': source,
                            'file_id': None,
                            'uuid': final_entries[idx]['uuid'],
                            'index': idx,
                            'error_message': msg or error_message,
                        }
                        for idx, msg in error_entries_indices
                    ])
                    continue

                validations.append({
                    'source': source,
                    'file_id': None,
                    'directives': directives,
                    'text': formatted_text,
                    'entry_count': len(final_entries),
                })
                continue

            parse_file, error_response = self.get_parse_file(request, file_id)
            if error_response:
                # 文件丢失：从队列移除该文件引用后跳过
                EntryReviewQueueService.remove_file(request.user.id, file_id)
                continue

            final_entries = ParseReviewService.get_final_result(file_id)
            if not final_entries:
                # 该文件无有效条目，记录待清理引用
                pending_removal.append((source, file_id))
                continue

            # 合并所有条目
            formatted_text = '\n\n'.join([
                entry['formatted'].rstrip() for entry in final_entries
            ])

            # 进行 Beancount 语法与金额校验
            is_valid, error_message, _ = BeancountValidator.validate_entries(
                formatted_text, check_balance=True
            )
            if not is_valid:
                # 逐条校验以定位错误条目
                entries_list = [entry['formatted'].rstrip() for entry in final_entries]
                _, _, error_entries_indices = BeancountValidator.validate_multiple_entries(
                    entries_list, check_balance=True
                )
                if not error_entries_indices:
                    # 整体校验失败但无法逐条定位时，指向首条，避免漏拦后继续写入
                    error_entries_indices = [(0, error_message or '校验未通过')]
                error_entries.extend([
                    {
                        'source': source,
                        'file_id': file_id,
                        'uuid': final_entries[idx]['uuid'],
                        'index': idx,
                        'error_message': msg or error_message,
                    }
                    for idx, msg in error_entries_indices
                ])
                continue

            validations.append({
                'source': source,
                'file_id': file_id,
                'parse_file': parse_file,
                'text': formatted_text,
                'entry_count': len(final_entries),
            })

        # 只要有任何错误，不写入任何文件、不改状态
        if error_entries:
            return Response(
                {
                    'error': f'Beancount 校验未通过: 共 {len(error_entries)} 条存在语法或金额错误',
                    'error_entries': error_entries,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 全部通过后再写入文件
        written_files = []
        copilot_written = False
        recurring_written = False
        try:
            for item in validations:
                if item['source'] == EntryReviewQueueService.SOURCE_COPILOT:
                    # Copilot 来源：追加写入 trans/collect.bean，不覆盖已有内容与注释头
                    BeanFileManager.append_to_collect_bean(request.user, item['directives'])
                    copilot_written = True
                    written_files.append({
                        'source': EntryReviewQueueService.SOURCE_COPILOT,
                        'entry_count': item['entry_count'],
                        'bean': 'trans/collect.bean',
                    })
                    continue

                if item['source'] == EntryReviewQueueService.SOURCE_RECURRING:
                    # 循环待办来源：追加写入 trans/collect.bean
                    BeanFileManager.append_to_collect_bean(request.user, item['directives'])
                    recurring_written = True
                    written_files.append({
                        'source': EntryReviewQueueService.SOURCE_RECURRING,
                        'entry_count': item['entry_count'],
                        'bean': 'trans/collect.bean',
                    })
                    continue

                parse_file = item['parse_file']
                bean_file_path = BeanFileManager.get_bean_file_path(
                    request.user, parse_file.file.name, parse_file.file.get_bean_dir()
                )
                with open(bean_file_path, 'w', encoding='utf-8') as f:
                    f.write(item['text'])

                parse_file.status = 'parsed'
                parse_file.save()

                written_files.append({
                    'source': EntryReviewQueueService.SOURCE_FILE,
                    'file_id': item['file_id'],
                    'entry_count': item['entry_count'],
                })
        except Exception as e:
            logger.error(f"确认写入失败: {str(e)}", exc_info=True)
            return Response(
                {'error': f'写入文件失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # 写入后维护对账条目：为被后补交易打破的 balance 断言补 pad，
        # 并注释因真实交易落入断言容差而变为无用的 pad（best-effort）
        from project.apps.reconciliation.services.reconciliation_comment_service import (
            ReconciliationCommentService,
        )
        ReconciliationCommentService.maintain_ledger(request.user)

        # 写入成功后删除 Copilot 暂存区
        if copilot_written:
            CopilotBookkeepingService.clear(request.user.id)

        # 写入成功后删除循环待办暂存区
        if recurring_written:
            RecurringBookkeepingService.clear(request.user.id)

        # 清理无有效条目的文件引用后清空队列并完成待办
        for source, file_id in pending_removal:
            if source == EntryReviewQueueService.SOURCE_FILE:
                EntryReviewQueueService.remove_file(request.user.id, file_id)
            elif source == EntryReviewQueueService.SOURCE_RECURRING:
                RecurringBookkeepingService.clear(request.user.id)
        EntryReviewQueueService.clear(request.user.id)
        EntryReviewQueueService.complete_task(request.user)

        return Response({
            'message': '确认写入成功',
            'files': written_files,
        }, status=status.HTTP_200_OK)


class ParseTaskStatusView(APIView):
    """查询单个 Celery 解析任务状态（与 task_status:{task_id} 缓存一致）"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        celery_task_id = request.query_params.get('task_id')
        if not celery_task_id:
            return Response(
                {'error': '缺少 task_id 参数'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task_status = cache.get(f'task_status:{celery_task_id}') or {'status': 'unknown'}
        file_id = task_status.get('file_id')
        if file_id is not None:
            file_obj = None
            try:
                from project.apps.file_manager.models import File
                file_obj = File.objects.filter(id=file_id, owner=request.user).first()
            except Exception:
                file_obj = None
            if file_obj is None:
                return Response(
                    {'error': '无权访问该任务'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        return Response({
            'task_id': celery_task_id,
            'file_id': file_id,
            'status': task_status.get('status', 'unknown'),
            'error': task_status.get('error'),
        }, status=status.HTTP_200_OK)


class EntryReviewReparseAllView(EntryReviewViewSet):
    """重新解析某文件的所有条目"""

    def post(self, request):
        """重新解析所有条目

        POST /api/translate/entry-review/reparse-all
        Body: {"file_id": ..., "source": "file|copilot"}
        """
        source = self.get_source(request, request.data)
        if source == self.SOURCE_COPILOT:
            return Response(
                {'error': 'Copilot 记账条目不支持重新解析，请直接编辑条目文本'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if source == self.SOURCE_RECURRING:
            return Response(
                {'error': '循环待办条目不支持重新解析，请直接编辑条目文本'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_id = request.data.get('file_id')
        parse_file, error_response = self.get_parse_file(request, file_id)
        if error_response:
            return error_response

        task = self.get_review_task(request)
        if task is None or task.status != 'pending':
            return Response(
                {'error': '该待办已完成或已取消'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 重新执行解析任务（相当于在文件管理中再次解析）
        from project.apps.translate.tasks import parse_single_file_task

        try:
            # 更新文件状态为待解析
            parse_file.status = 'pending'
            parse_file.save()

            # 创建解析任务（审核模式）
            password = request.data.get('password') or None
            if password == '':
                password = None
            args = {
                'write': False,  # 审核模式
                'cmb_credit_ignore': True,
                'boc_debit_ignore': True,
                'password': password,
            }

            # 异步执行解析任务
            async_result = parse_single_file_task.delay(parse_file.file_id, request.user.id, args)
            cache.set(f'task_status:{async_result.id}', {
                'status': 'pending',
                'file_id': parse_file.file_id,
                'error': None,
            }, timeout=24 * 3600)

            return Response({
                'message': '重新解析任务已提交',
                'file_id': parse_file.file_id,
                'celery_task_id': async_result.id,
            }, status=status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.error(f"重新解析失败: {str(e)}", exc_info=True)
            return Response(
                {'error': f'重新解析失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BillSupportStatusView(APIView):
    """各账单类型解析可用性（最近成功解析时间与账单最新交易日期）"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'bills': build_bill_support_snapshot()}, status=status.HTTP_200_OK)
