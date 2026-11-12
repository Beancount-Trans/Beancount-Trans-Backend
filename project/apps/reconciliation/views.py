"""
对账模块的视图集
"""
import logging
from datetime import date

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.authentication import JWTAuthentication
from django_filters.rest_framework import DjangoFilterBackend

from project.apps.common.permissions import IsOwnerOrAdminReadWriteOnly
from project.apps.common.filters import ScheduledTaskUserFilterBackend
from project.apps.translate.models import FormatConfig

from .models import ScheduledTask, RecurringTask
from .serializers import (
    ScheduledTaskSerializer,
    ScheduledTaskListSerializer,
    ScheduledTaskUpdateSerializer,
    ReconciliationStartSerializer,
    ReconciliationExecuteSerializer,
    ReconciliationExecuteResponseSerializer,
    RecurringTaskSerializer,
)
from .services import (
    BalanceCalculationService,
    ReconciliationService,
    AccountCurrencyService,
    RecurringTaskService,
)
from .services.recurrence import RecurrenceParseError

logger = logging.getLogger(__name__)


class ScheduledTaskViewSet(ModelViewSet):
    """
    待办任务视图集
    
    提供待办任务的增删改查功能，以及对账相关的自定义 action
    """
    queryset = ScheduledTask.objects.all()
    serializer_class = ScheduledTaskSerializer
    permission_classes = [IsOwnerOrAdminReadWriteOnly]
    authentication_classes = [JWTAuthentication]
    filter_backends = [ScheduledTaskUserFilterBackend, DjangoFilterBackend]
    
    def get_serializer_class(self):
        """根据操作类型返回不同的序列化器"""
        if self.action == 'list':
            return ScheduledTaskListSerializer
        elif self.action == 'retrieve':
            return ScheduledTaskListSerializer
        elif self.action in ['update', 'partial_update']:
            return ScheduledTaskUpdateSerializer
        elif self.action == 'start':
            return ReconciliationStartSerializer
        elif self.action == 'execute':
            return ReconciliationExecuteSerializer
        return ScheduledTaskSerializer

    def perform_update(self, serializer):
        """更新待办；循环待办改期时顺延其定义（取消旧实例并按新日期对齐）"""
        task = serializer.instance
        new_date = serializer.validated_data.get('scheduled_date')
        serializer.save()

        defn = task.content_object
        if (
            new_date is not None
            and task.task_type == 'recurring'
            and isinstance(defn, RecurringTask)
            and defn.status == 'active'
        ):
            RecurringTaskService.reschedule(defn, new_date)
    
    def get_queryset(self):
        """获取查询集
        
        用户过滤由 ScheduledTaskUserFilterBackend 处理
        可通过查询参数过滤：
        - status: 状态筛选（pending/completed/cancelled）
        - task_type: 任务类型筛选（reconciliation/ai_feedback）
        - due: 是否到期（true 返回 scheduled_date <= today 的待办）
        """
        queryset = super().get_queryset()  # 过滤器后端已处理用户过滤
        
        # 到期筛选（优先处理，因为会限制状态）
        due = self.request.query_params.get('due')
        if due and due.lower() == 'true':
            # 到期筛选：scheduled_date <= today 且 status = pending
            queryset = queryset.filter(
                scheduled_date__lte=date.today(),
                status='pending'
            )
        else:
            # 如果没有 due 参数，才应用 status 筛选
            status_param = self.request.query_params.get('status')
            if status_param:
                queryset = queryset.filter(status=status_param)
        
        # 任务类型筛选
        task_type = self.request.query_params.get('task_type')
        if task_type:
            queryset = queryset.filter(task_type=task_type)
        
        return queryset.select_related('content_type').order_by('scheduled_date')
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """开始对账：计算并返回预期余额"""
        task = self.get_object()
        
        if task.task_type != 'reconciliation':
            return Response(
                {'error': '该待办不是对账类型'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if task.status != 'pending':
            return Response(
                {'error': '该待办已完成或已取消'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        account = task.content_object
        
        # 计算预期余额（只计算到当天的记录，明天之后的记录不计入）
        as_of_date = date.today()
        balances = BalanceCalculationService.calculate_balance(
            account.owner,
            account.account,
            as_of_date=as_of_date
        )
        
        # 确定可对账币种：账户配置币种优先（含 0 余额），未配置时回退账本非零余额
        currency_balances = AccountCurrencyService.resolve_reconciliation_currencies(
            account, balances
        )

        configured = AccountCurrencyService.get_configured_currencies(account)
        if configured:
            extra = set(balances) - set(configured)
            if extra:
                logger.warning(
                    "账户 %s 配置币种 %s，但账本存在额外币种 %s，对账时已忽略",
                    account.account, configured, sorted(extra),
                )

        # 确定默认币种：优先选择CNY，否则选择第一个币种
        default_currency = None
        if currency_balances:
            codes = [item['currency'] for item in currency_balances]
            default_currency = 'CNY' if 'CNY' in codes else codes[0]
        
        # 获取上一次对账日期（最近一次完成的对账任务的 as_of_date）
        from django.contrib.contenttypes.models import ContentType
        from project.apps.account.models import Account
        account_content_type = ContentType.objects.get_for_model(Account)
        last_reconciliation = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=account_content_type,
            object_id=account.id,
            status='completed',
            as_of_date__isnull=False
        ).exclude(id=task.id).order_by('-as_of_date').first()
        
        last_reconciliation_date = last_reconciliation.as_of_date if last_reconciliation else None
        last_completed_task_id = last_reconciliation.id if last_reconciliation else None

        # 默认对账时间点：沿用当天首条已完成的对账待办；当天没有时沿用上一条已完成的对账待办
        # as_of_date 比完成日期早一天，说明当时选的是「第二天第一笔交易前」
        user_account_ids = Account.objects.filter(owner=request.user).values_list('id', flat=True)
        completed_tasks = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=account_content_type,
            object_id__in=user_account_ids,
            status='completed',
            completed_date__isnull=False,
            as_of_date__isnull=False,
        )
        anchor_task = (
            completed_tasks.filter(completed_date=date.today()).order_by('id').first()
            or completed_tasks.order_by('-completed_date', '-id').first()
        )
        default_reconciliation_timing = 'end_of_day'
        if anchor_task and anchor_task.as_of_date < anchor_task.completed_date:
            default_reconciliation_timing = 'start_of_next_day'

        # 若当前任务来自撤销（revoked_task 指向被撤销任务），返回该任务的 transaction_items 用于预填
        last_reconciliation_transaction_items = None
        if getattr(task, 'revoked_task_id', None) and task.revoked_task_id:
            revoked = task.revoked_task
            if revoked and getattr(revoked, 'reconciliation_transaction_items', None):
                last_reconciliation_transaction_items = revoked.reconciliation_transaction_items

        config = FormatConfig.get_user_config(request.user)
        is_first_reconciliation = account.is_first_reconciliation()
        if is_first_reconciliation:
            default_allocation_account = 'Equity:Opening-Balances'
        else:
            default_allocation_account = (
                config.reconciliation_fallback_account or 'Equity:Adjustments'
            )

        data = {
            'balances': currency_balances,
            'account_name': account.account,
            'as_of_date': date.today(),
            'default_currency': default_currency,
            'is_first_reconciliation': is_first_reconciliation,
            'default_allocation_account': default_allocation_account,
            'last_reconciliation_date': last_reconciliation_date,
            'last_completed_task_id': last_completed_task_id,
            'default_reconciliation_timing': default_reconciliation_timing,
            'last_reconciliation_transaction_items': last_reconciliation_transaction_items or [],
        }
        
        serializer = ReconciliationStartSerializer(data)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        """执行对账：处理差额，生成指令，更新状态，创建下一个待办"""
        task = self.get_object()
        
        if task.task_type != 'reconciliation':
            return Response(
                {'error': '该待办不是对账类型'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if task.status != 'pending':
            return Response(
                {'error': '该待办已完成或已取消'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 验证请求数据
        serializer = ReconciliationExecuteSerializer(
            data=request.data,
            context={'task': task}
        )
        serializer.is_valid(raise_exception=True)
        
        # 执行对账
        try:
            result = ReconciliationService.execute_reconciliation(
                task=task,
                actual_balance=serializer.validated_data['actual_balance'],
                currency=serializer.validated_data.get('currency', 'CNY'),
                transaction_items=serializer.validated_data.get('transaction_items', []),
                as_of_date=serializer.validated_data.get('as_of_date')
            )
            
            response_serializer = ReconciliationExecuteResponseSerializer(result)
            return Response(response_serializer.data)
            
        except Exception as e:
            logger.error(f"执行对账失败: {e}", exc_info=True)
            return Response(
                {'error': f'执行对账失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def revoke_reconciliation(self, request, pk=None):
        """撤销对账：注释当次条目、更新任务状态、更新或新建待办"""
        task = self.get_object()
        
        if task.task_type != 'reconciliation':
            return Response(
                {'error': '该待办不是对账类型'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if task.status != 'completed':
            return Response(
                {'error': '只能撤销已完成的对账待办'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            result = ReconciliationService.revoke_reconciliation(task)
            return Response(result)
        except ValueError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"撤销对账失败: {e}", exc_info=True)
            return Response(
                {'error': f'撤销对账失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def complete(self, request, pk=None):
        """完成循环待办实例，并按基准推进其定义的下次生成日期"""
        task = self.get_object()

        if task.task_type != 'recurring':
            return Response(
                {'error': '该待办不是循环待办'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if task.status != 'pending':
            return Response(
                {'error': '该待办已完成或已取消'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            result = RecurringTaskService.complete_instance(task)
            return Response({
                'completed': result['completed'],
                'next_date': result['next_date'],
            })
        except Exception as e:
            logger.error(f"完成循环待办失败: {e}", exc_info=True)
            return Response(
                {'error': f'完成循环待办失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RecurringTaskViewSet(ModelViewSet):
    """循环待办定义视图集

    提供循环待办定义的增删改查，以及预览等自定义 action。
    """
    queryset = RecurringTask.objects.all()
    serializer_class = RecurringTaskSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]
    pagination_class = None

    def get_queryset(self):
        """仅返回当前用户的循环待办定义"""
        return RecurringTask.objects.filter(user=self.request.user)

    def perform_destroy(self, instance):
        """删除定义，同时取消其所有待执行的实例"""
        RecurringTaskService.delete(instance, cancel_pending=True)

    @action(detail=False, methods=['post'], url_path='preview')
    def preview_rule(self, request):
        """即时预览规则文本（未持久化的 DSL）"""
        rule_text = request.data.get('rule_text', '')
        start_date = request.data.get('start_date')
        kind = request.data.get('kind', 'notify')
        try:
            result = RecurringTaskService.preview_text(
                rule_text,
                start_date=date.fromisoformat(start_date) if start_date else None,
                kind=kind,
            )
        except RecurrenceParseError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        return Response(result)

