"""
对账模块的序列化器
"""
from rest_framework import serializers
from decimal import Decimal
from datetime import timedelta
from django.db import models

from .models import ScheduledTask, RecurringTask


class ScheduledTaskSerializer(serializers.ModelSerializer):
    """ScheduledTask 基础序列化器"""
    task_type_display = serializers.CharField(source='get_task_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = ScheduledTask
        fields = [
            'id', 'task_type', 'task_type_display',
            'content_type', 'object_id',
            'scheduled_date', 'completed_date',
            'status', 'status_display',
            'created', 'modified'
        ]
        read_only_fields = ['id', 'created', 'modified', 'task_type_display', 'status_display']


class ScheduledTaskListSerializer(serializers.ModelSerializer):
    """ScheduledTask 列表序列化器（包含关联对象信息）"""
    task_type_display = serializers.CharField(source='get_task_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    # 账户信息（对账待办）
    account_name = serializers.SerializerMethodField()
    account_type = serializers.SerializerMethodField()
    
    # 缓存审核截止时间（条目审核待办）
    review_expires_at = serializers.SerializerMethodField()
    
    # 待审核条目数量（条目审核待办）
    entry_count = serializers.SerializerMethodField()

    # 循环待办展示标签（循环待办实例）
    recurring_label = serializers.SerializerMethodField()

    # 循环待办「本次待办完成后的下一次待办生成日期」（循环待办实例）
    next_run_date = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledTask
        fields = [
            'id', 'task_type', 'task_type_display',
            'scheduled_date', 'completed_date',
            'status', 'status_display',
            'account_name', 'account_type',
            'review_expires_at', 'entry_count',
            'recurring_label', 'next_run_date',
            'created', 'modified'
        ]
        read_only_fields = ['id', 'created', 'modified']
    
    def get_account_name(self, obj):
        """获取关联账户名称"""
        from project.apps.account.models import Account
        if isinstance(obj.content_object, Account):
            return obj.content_object.account
        return None
    
    def get_account_type(self, obj):
        """获取账户类型"""
        from project.apps.account.models import Account
        if isinstance(obj.content_object, Account):
            return obj.content_object.get_account_type()
        return None
    
    def get_review_expires_at(self, obj):
        """获取用户审核截止时间（条目审核待办）"""
        if obj.task_type == 'entry_review':
            # 条目审核待办：返回用户统一队列中最早到期时间
            from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
            return EntryReviewQueueService.earliest_expires_at(obj.object_id)
        return None
    
    def get_entry_count(self, obj):
        """获取待审核条目数量（仅条目审核待办返回，否则为 None）"""
        if obj.task_type == 'entry_review':
            from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
            return len(EntryReviewQueueService.list_refs(obj.object_id))
        return None

    def get_recurring_label(self, obj):
        """获取循环待办定义的展示标签（仅循环待办实例返回，否则为 None）"""
        if isinstance(obj.content_object, RecurringTask):
            from .services import RecurringTaskService
            return RecurringTaskService.label(obj.content_object)
        return None

    def get_next_run_date(self, obj):
        """获取「本次待办完成后的下一次待办生成日期」（仅循环待办实例返回，否则为 None）"""
        if isinstance(obj.content_object, RecurringTask):
            from .services import RecurringTaskService
            nxt = RecurringTaskService.display_next_date(obj.content_object)
            return nxt.isoformat() if nxt else None
        return None


class ScheduledTaskUpdateSerializer(serializers.ModelSerializer):
    """ScheduledTask 更新序列化器（仅允许修改 scheduled_date）"""
    
    class Meta:
        model = ScheduledTask
        fields = ['scheduled_date']


class RecurringTaskSerializer(serializers.ModelSerializer):
    """循环待办定义序列化器"""

    # 只读计算字段：后续发生日期（ISO 字符串）与规则描述
    next_occurrences = serializers.SerializerMethodField()
    describe = serializers.SerializerMethodField()

    # 只读计算字段：管理页展示用的「下一次执行日期」（见 get_next_run_date）
    next_run_date = serializers.SerializerMethodField()

    # 只读计算字段：是否存在待执行实例（决定管理页日期是否可编辑）
    has_pending = serializers.SerializerMethodField()

    # 可选字段：创建时未提供 start_date 时由服务默认取当天
    start_date = serializers.DateField(required=False, allow_null=True)
    recurrence_basis = serializers.ChoiceField(
        choices=RecurringTask.RECURRENCE_BASIS_CHOICES,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = RecurringTask
        fields = [
            'id', 'note', 'kind', 'template',
            'rule_text', 'rrule',
            'recurrence_basis', 'start_date', 'next_run_date', 'has_pending', 'status',
            'generated_count', 'next_occurrences', 'describe',
            'created', 'modified'
        ]
        read_only_fields = [
            'id', 'rrule', 'next_run_date', 'has_pending', 'status', 'generated_count',
            'next_occurrences', 'describe', 'created', 'modified'
        ]

    def get_next_run_date(self, obj):
        """管理页展示用的「下一次执行日期」（动态推算，见 RecurringTaskService.display_next_date）"""
        from .services import RecurringTaskService
        nxt = RecurringTaskService.display_next_date(obj)
        return nxt.isoformat() if nxt else None

    def get_has_pending(self, obj):
        """是否存在待执行实例（存在 = 管理页日期只读，只能改实例日期）"""
        from .services import RecurringTaskService
        return RecurringTaskService.has_pending(obj)

    def get_next_occurrences(self, obj):
        """预览定义的后续发生日期（最多 5 次），失败时返回空列表"""
        from .services import RecurringTaskService
        try:
            return [d.isoformat() for d in RecurringTaskService.preview(obj, count=5)]
        except Exception:
            return []

    def get_describe(self, obj):
        """解析规则原文并返回可读描述，失败时返回空字符串"""
        from .services.recurrence import RecurrenceParser
        try:
            return RecurrenceParser.parse(obj.rule_text).describe()
        except Exception:
            return ''

    def validate_rule_text(self, value):
        """校验循环规则原文，无法解析时抛出校验错误"""
        from .services.recurrence import RecurrenceParser, RecurrenceParseError
        try:
            RecurrenceParser.parse(value)
        except RecurrenceParseError as e:
            raise serializers.ValidationError(str(e))
        return value

    def validate(self, attrs):
        """到期产出为 generate_review 时，模板不能为空（不做语法校验）"""
        kind = attrs.get('kind') or getattr(self.instance, 'kind', 'notify')
        if kind == 'generate_review':
            template = attrs.get('template')
            if template is None:
                template = getattr(self.instance, 'template', '') or ''
            if not str(template).strip():
                raise serializers.ValidationError(
                    {'template': '生成审核待办时模板不能为空'}
                )
        return attrs

    def create(self, validated_data):
        """通过服务创建循环待办定义（归一化 rrule、初始化 next_date）"""
        from .services import RecurringTaskService
        user = self.context['request'].user
        return RecurringTaskService.create(
            user=user,
            rule_text=validated_data['rule_text'],
            note=validated_data.get('note', ''),
            start_date=validated_data.get('start_date'),
            basis=validated_data.get('recurrence_basis'),
            kind=validated_data.get('kind') or 'notify',
            template=validated_data.get('template', '') or '',
        )

    def update(self, instance, validated_data):
        """通过服务更新循环待办定义（仅允许白名单字段）"""
        from .services import RecurringTaskService
        kwargs = {}
        for key in ('note', 'kind', 'template', 'rule_text',
                    'start_date', 'recurrence_basis'):
            if key in validated_data:
                kwargs[key] = validated_data[key]
        return RecurringTaskService.update(instance, **kwargs)


class TransactionItemSerializer(serializers.Serializer):
    """Transaction 条目序列化器"""
    account = serializers.CharField(max_length=128, help_text="账户路径")
    amount = serializers.DecimalField(
        max_digits=19, 
        decimal_places=2, 
        required=False, 
        allow_null=True,
        help_text="金额（留空时为自动计算）"
    )
    is_auto = serializers.BooleanField(
        default=False,
        help_text="是否为自动计算"
    )
    date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="交易日期（仅 is_auto=false 时可指定，不能超过 as_of_date）"
    )
    
    def validate(self, data):
        """验证日期字段规则"""
        is_auto = data.get('is_auto', False)
        item_date = data.get('date')
        
        if is_auto and item_date is not None:
            raise serializers.ValidationError('is_auto=true 的条目不允许指定日期')
        
        return data


class CurrencyBalanceSerializer(serializers.Serializer):
    """币种余额序列化器"""
    currency = serializers.CharField(
        max_length=10,
        help_text="币种"
    )
    expected_balance = serializers.DecimalField(
        max_digits=19, 
        decimal_places=2,
        help_text="预期余额"
    )


class ReconciliationStartSerializer(serializers.Serializer):
    """开始对账响应序列化器"""
    balances = CurrencyBalanceSerializer(
        many=True,
        help_text="账户可对账币种及预期余额（账户配置币种优先，未配置时返回余额非零的账本币种）"
    )
    account_name = serializers.CharField(
        max_length=128,
        help_text="账户路径"
    )
    as_of_date = serializers.DateField(
        help_text="截止日期"
    )
    default_currency = serializers.CharField(
        max_length=10,
        required=False,
        allow_null=True,
        help_text="默认币种（如果存在CNY则返回CNY，否则返回第一个币种）"
    )
    is_first_reconciliation = serializers.BooleanField(
        help_text="是否首次对账（True=首次固定 Equity:Opening-Balances；False=后续使用 default_allocation_account）"
    )
    default_allocation_account = serializers.CharField(
        max_length=128,
        help_text="差额分配默认账户（首次为 Equity:Opening-Balances，非首次为用户配置的兜底账户）"
    )
    last_reconciliation_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="上一次对账日期（如果存在，transaction_items 的日期必须在此日期之后）"
    )
    last_completed_task_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="该账户最近一条已完成对账任务 id，用于撤销入口"
    )
    default_reconciliation_timing = serializers.ChoiceField(
        choices=['end_of_day', 'start_of_next_day'],
        help_text="默认对账时间点：沿用当天首条已完成对账，当天没有时沿上一条已完成对账"
    )
    last_reconciliation_transaction_items = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_null=True,
        help_text="撤销后预填的差额分配条目（来自被撤销任务的 reconciliation_transaction_items）"
    )


class ReconciliationExecuteSerializer(serializers.Serializer):
    """执行对账请求序列化器"""
    actual_balance = serializers.DecimalField(
        max_digits=19, 
        decimal_places=2,
        help_text="实际余额"
    )
    currency = serializers.CharField(
        max_length=10,
        required=False,
        help_text="币种（缺省时按账户配置币种优先、账本余额兜底推导）"
    )
    transaction_items = TransactionItemSerializer(
        many=True, 
        required=False,
        help_text="差额分配条目（有差额时使用）"
    )
    as_of_date = serializers.DateField(
        required=True,
        help_text="对账截止日期（必须由前端提供）"
    )
    
    def validate(self, data):
        """验证对账数据"""
        from .validators import ReconciliationValidator
        from .services import AccountCurrencyService, BalanceCalculationService
        from .models import ScheduledTask
        
        # 获取任务和账户信息
        task = self.context.get('task')
        if not task:
            raise serializers.ValidationError('未提供待办')
        
        account = task.content_object
        
        # 计算预期余额（使用 as_of_date，必须由前端提供）
        as_of_date = data.get('as_of_date')
        if not as_of_date:
            raise serializers.ValidationError('as_of_date 必须由前端提供')
        
        # 检查提交的 as_of_date 是否大于已完成的 as_of_date
        # 获取该账户已完成的对账记录中最大的 as_of_date
        max_completed_as_of_date = ScheduledTask.objects.filter(
            task_type='reconciliation',
            content_type=task.content_type,
            object_id=task.object_id,
            status='completed',
            as_of_date__isnull=False
        ).exclude(id=task.id).aggregate(
            max_date=models.Max('as_of_date')
        )['max_date']
        
        if max_completed_as_of_date:
            if as_of_date == max_completed_as_of_date:
                raise serializers.ValidationError(
                    f'该账户在 {as_of_date} 已完成对账，不允许重复对账同一日期'
                )
            elif as_of_date < max_completed_as_of_date:
                raise serializers.ValidationError(
                    f'对账日期 {as_of_date} 不能早于上一次对账日期 {max_completed_as_of_date}，只能对账未来的日期'
                )
        balances = BalanceCalculationService.calculate_balance(
            account.owner,
            account.account,
            as_of_date=as_of_date
        )

        # 币种优先取账户配置，未配置时回退账本余额推导
        configured = AccountCurrencyService.get_configured_currencies(account)
        currency = data.get('currency')
        if not currency:
            candidates = configured or list(balances.keys())
            currency = 'CNY' if 'CNY' in candidates else (candidates[0] if candidates else 'CNY')
            data['currency'] = currency

        if configured and currency not in configured:
            raise serializers.ValidationError(
                {'currency': f'币种 {currency} 不在账户可用币种 {",".join(configured)} 中'}
            )

        expected_balance = balances.get(currency, Decimal('0.00'))
        
        # 验证对账数据
        actual_balance = data['actual_balance']
        transaction_items = data.get('transaction_items', [])
        
        # 验证 transaction_items 中的账户不能与对账账户相同
        account_errors = []
        reconciliation_account = account.account
        for i, item in enumerate(transaction_items):
            item_account = item.get('account')
            if item_account and item_account == reconciliation_account:
                account_errors.append(f'条目 {i+1} 的账户不能与对账账户相同（{reconciliation_account}）')
        
        if account_errors:
            raise serializers.ValidationError({'transaction_items': account_errors})
        
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
        
        # 验证 transaction_items 中的日期
        date_errors = []
        for i, item in enumerate(transaction_items):
            item_date = item.get('date')
            is_auto = item.get('is_auto', False)
            
            if not is_auto and item_date is not None:
                # is_auto=false 的条目，日期不能超过 as_of_date
                if item_date > as_of_date:
                    date_errors.append(f'条目 {i+1} 的日期 {item_date} 不能超过对账截止日期 {as_of_date}')
                
                # 日期不能早于上一次对账日期（如果存在）
                if last_reconciliation_date and item_date <= last_reconciliation_date:
                    date_errors.append(
                        f'条目 {i+1} 的日期 {item_date} 不能早于或等于上一次对账日期 {last_reconciliation_date}（必须在 {last_reconciliation_date + timedelta(days=1)} 或之后）'
                    )
        
        if date_errors:
            raise serializers.ValidationError({'transaction_items': date_errors})
        
        # 转换为字典列表
        transaction_items_dict = [
            {
                'account': item.get('account'),
                'amount': item.get('amount'),
                'is_auto': item.get('is_auto', False)
            }
            for item in transaction_items
        ]
        
        is_valid, errors = ReconciliationValidator.validate_reconciliation_data(
            actual_balance,
            expected_balance,
            transaction_items_dict
        )
        
        if not is_valid:
            raise serializers.ValidationError({'transaction_items': errors})
        
        return data


class ReconciliationExecuteResponseSerializer(serializers.Serializer):
    """执行对账响应序列化器"""
    status = serializers.CharField(help_text="执行状态")
    directives = serializers.ListField(
        child=serializers.CharField(),
        help_text="生成的 Beancount 指令列表"
    )
    next_task_id = serializers.IntegerField(
        allow_null=True,
        help_text="下一个待办任务 ID"
    )



