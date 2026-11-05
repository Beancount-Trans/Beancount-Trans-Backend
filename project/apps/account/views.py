from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.db import transaction
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
# from django.shortcuts import get_object_or_404
from django.apps import apps

from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem
from project.apps.account.serializers import (
    AccountSerializer, AccountTreeSerializer,
    AccountBatchUpdateSerializer, AccountMigrationSerializer, AccountDeleteSerializer,
    AccountReconcileApplySerializer,
    AccountTemplateItemSerializer, AccountTemplateListSerializer, 
    AccountTemplateDetailSerializer, AccountTemplateApplySerializer
)
from project.apps.account.filters import AccountTypeFilter
from django_filters.rest_framework import DjangoFilterBackend
from project.apps.common.permissions import IsOwnerOrAdminReadWriteOnly, AnonymousReadOnlyPermission, TemplatePermission
from project.apps.common.filters import CurrentUserFilterBackend, AnonymousUserFilterBackend


class AccountViewSet(ModelViewSet):
    """
    账户管理视图集

    提供基于Beancount的树形账户管理功能，包括：
    - 账户的增删改查
    - 树形结构展示
    - 账户迁移
    - 账户映射

    支持匿名用户访问id=1用户的数据（只读）
    """
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [AnonymousReadOnlyPermission]
    filter_backends = [AnonymousUserFilterBackend, DjangoFilterBackend]
    filterset_class = AccountTypeFilter
    authentication_classes = [JWTAuthentication]
    search_fields = ['account']
    ordering_fields = ['account', 'created', 'modified']
    ordering = ['account']

    def get_serializer_class(self):
        """根据操作类型返回不同的序列化器"""
        if self.action == 'tree':
            return AccountTreeSerializer
        elif self.action == 'batch_update':
            return AccountBatchUpdateSerializer
        elif self.action == 'migrate':
            return AccountMigrationSerializer
        return AccountSerializer

    def get_queryset(self):
        """获取查询集"""
        queryset = super().get_queryset()

        # 根据用户过滤，匿名用户使用id=1用户的数据
        # User = get_user_model()
        # if self.request.user.is_authenticated:
        #     queryset = queryset.filter(owner=self.request.user)
        # else:
        #     # 匿名用户使用id=1用户的账户
        #     try:
        #         default_user = User.objects.get(id=1)
        #         queryset = queryset.filter(owner=default_user)
        #     except User.DoesNotExist:
        #         queryset = queryset.none()

        # 根据账户类型过滤
        account_type = self.request.query_params.get('account_type')
        if account_type:
            queryset = queryset.filter(account__startswith=account_type)

        # 根据启用状态过滤
        enable = self.request.query_params.get('enable')
        if enable is not None:
            queryset = queryset.filter(enable=enable.lower() == 'true')

        return queryset.select_related('parent', 'owner')

    def perform_create(self, serializer):
        """创建账户时设置属主"""
        serializer.save(owner=self.request.user)
    
    def perform_update(self, serializer):
        """更新账户时处理对账周期规则变更"""
        from datetime import date
        from django.contrib.contenttypes.models import ContentType
        from project.apps.reconciliation.models import ScheduledTask

        instance = self.get_object()
        old_rule = instance.reconciliation_rule

        # 保存更新
        updated_instance = serializer.save()

        new_rule = updated_instance.reconciliation_rule

        # 处理对账周期规则变更
        account_content_type = ContentType.objects.get_for_model(Account)

        # 场景1：设置规则（从无到有）
        if not old_rule and new_rule:
            # 创建首个待办，执行日期为今日
            ScheduledTask.objects.create(
                task_type='reconciliation',
                content_type=account_content_type,
                object_id=updated_instance.id,
                scheduled_date=date.today(),
                status='pending'
            )

        # 场景2：删除规则（从有到无）
        elif old_rule and not new_rule:
            # 将相关待办的状态更新为 cancelled
            ScheduledTask.objects.filter(
                task_type='reconciliation',
                content_type=account_content_type,
                object_id=updated_instance.id,
                status='pending'
            ).update(status='cancelled')

        # 场景3：修改规则（从有到有）
        # 相关待办不自动更新，保持原有 scheduled_date

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """获取账户树形结构"""
        # 应用 filter_backends 确保只返回当前用户的数据
        queryset = self.filter_queryset(self.get_queryset())
        root_accounts = queryset.filter(parent__isnull=True)

        serializer = self.get_serializer(root_accounts, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def reconcile(self, request):
        """对比账本 open 账户与平台账户，返回改名/新增候选"""
        if not request.user.is_authenticated:
            return Response(
                {'detail': '需要登录'}, status=status.HTTP_403_FORBIDDEN
            )

        from project.apps.account.account_reconcile import diff_ledger_accounts

        return Response(diff_ledger_accounts(request.user))

    @action(detail=False, methods=['post'], url_path='reconcile/apply')
    def reconcile_apply(self, request):
        """确认旧 → 新配对，更新平台账户（仅 Git 用户）"""
        if not request.user.is_authenticated:
            return Response(
                {'detail': '需要登录'}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = AccountReconcileApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from project.apps.account.account_reconcile import apply_pairings

        try:
            applied = apply_pairings(request.user, serializer.validated_data['pairs'])
        except DjangoValidationError as e:
            raise ValidationError({'detail': list(e.messages)})

        return Response({'applied': applied})

    @action(detail=True, methods=['get'])
    def mappings(self, request, pk=None):
        """获取账户相关的映射"""
        account = self.get_object()

        try:
            Expense = apps.get_model('maps', 'Expense')
            Assets = apps.get_model('maps', 'Assets')
            Income = apps.get_model('maps', 'Income')

            # 获取当前用户的映射，匿名用户使用id=1用户的映射
            User = get_user_model()
            if request.user.is_authenticated:
                user = request.user
            else:
                # 匿名用户使用id=1用户的映射
                try:
                    user = User.objects.get(id=1)
                except User.DoesNotExist:
                    return Response(
                        {'error': '默认用户（ID=1）不存在'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

            expense_mappings = Expense.objects.filter(
                expend=account, 
                owner=user, 
                # enable=True
            )
            assets_mappings = Assets.objects.filter(
                assets=account, 
                owner=user, 
                # enable=True
            )
            income_mappings = Income.objects.filter(
                income=account, 
                owner=user, 
                # enable=True
            )

            result = {
                'account': account.account,
                'account_id': account.id,
                'reconciliation_rule': account.reconciliation_rule,
                'reconciliation_cycle_display': account.get_reconciliation_cycle_display(),
                'expense_mappings': [
                    {
                        'id': m.id, 
                        'key': m.key, 
                        'payee': m.payee,
                        'currency': m.currency,
                        'enable': m.enable
                    }
                    for m in expense_mappings
                ],
                'assets_mappings': [
                    {
                        'id': m.id, 
                        'key': m.key, 
                        'full': m.full,
                        'enable': m.enable
                    }
                    for m in assets_mappings
                ],
                'income_mappings': [
                    {
                        'id': m.id, 
                        'key': m.key, 
                        'payer': m.payer,
                        'enable': m.enable
                    }
                    for m in income_mappings
                ],
                'total_count': expense_mappings.count() + assets_mappings.count() + income_mappings.count()
            }

            return Response(result)
        except Exception as e:
            return Response(
                {'error': f'获取映射失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def destroy(self, request, pk=None):
        """删除账户（有映射时需要提供迁移目标账户）"""
        account = self.get_object()

        # 验证请求数据
        serializer = AccountDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        migrate_to_id = serializer.validated_data.get('migrate_to')
        migrate_to = None

        # 如果提供了迁移目标账户ID，获取账户对象
        if migrate_to_id:
            try:
                migrate_to = Account.objects.get(id=migrate_to_id, owner=request.user)
            except Account.DoesNotExist:
                return Response(
                    {'error': '目标账户不存在或无权访问'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            with transaction.atomic():
                result = account.delete_with_migration(migrate_to=migrate_to)
                return Response({
                    'message': '账户删除成功',
                    'result': result
                })
        except ValidationError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': f'删除账户失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def migration_candidates(self, request, pk=None):
        """获取可用的迁移目标账户列表"""
        account = self.get_object()

        # 获取当前账户及其所有子账户的ID，用于排除
        def get_descendant_ids(acc):
            """递归获取账户的所有后代账户ID"""
            descendant_ids = [acc.id]
            for child in acc.children.all():
                descendant_ids.extend(get_descendant_ids(child))
            return descendant_ids

        # 排除当前账户及其所有子账户
        excluded_ids = get_descendant_ids(account)

        # 获取启用的账户作为迁移候选，排除当前账户及其子账户
        User = get_user_model()
        if request.user.is_authenticated:
            user = request.user
        else:
            # 匿名用户使用id=1用户的账户
            try:
                user = User.objects.get(id=1)
            except User.DoesNotExist:
                return Response(
                    {'error': '默认用户（ID=1）不存在'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        candidates = Account.objects.filter(
            owner=user,
            enable=True
        ).exclude(
            id__in=excluded_ids
        ).order_by('account')

        serializer = AccountSerializer(candidates, many=True, context={'request': request})
        return Response({
            'candidates': serializer.data,
            'current_account': {
                'id': account.id,
                'account': account.account,
                'account_type': account.get_account_type()
            }
        })


class AccountTemplateViewSet(ModelViewSet):
    """账户模板管理视图集"""
    permission_classes = [TemplatePermission]

    def get_serializer_class(self):
        """根据操作类型选择序列化器"""
        if self.action == 'list':
            return AccountTemplateListSerializer
        elif self.action == 'retrieve':
            return AccountTemplateDetailSerializer
        return AccountTemplateDetailSerializer

    def get_queryset(self):
        """获取查询集"""
        # 官方模板对所有用户可见
        queryset = AccountTemplate.objects.filter(is_official=True)

        # 登录用户可以看到自己的模板和公开模板
        if self.request.user.is_authenticated:
            user_templates = AccountTemplate.objects.filter(owner=self.request.user)
            public_templates = AccountTemplate.objects.filter(is_public=True, is_official=False)
            queryset = queryset | user_templates | public_templates

        return queryset.distinct()

    def perform_create(self, serializer):
        """创建时设置属主"""
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['post'])
    def apply(self, request, pk=None):
        """应用模板到用户账户

        请求体: {
            "action": "merge|overwrite",
            "conflict_resolution": "skip|overwrite"
        }
        """
        template = self.get_object()
        serializer = AccountTemplateApplySerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        action_type = data['action']
        conflict_resolution = data.get('conflict_resolution', 'skip')

        try:
            with transaction.atomic():
                result = self._apply_account_template(
                    template, 
                    request.user, 
                    action_type, 
                    conflict_resolution
                )
                return Response({
                    "message": "账户模板应用成功",
                    "result": result
                })
        except Exception as e:
            return Response(
                {'error': f'应用模板失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _apply_account_template(self, template, user, action_type, conflict_resolution):
        """应用账户模板到用户"""
        result = {
            'created': 0,
            'skipped': 0,
            'overwritten': 0
        }

        if action_type == 'overwrite':
            # 删除用户现有的所有账户
            Account.objects.filter(owner=user).delete()
            # 直接创建所有模板账户，无需检查冲突
            for item in template.items.all():
                Account.objects.create(
                    owner=user,
                    account=item.account_path,
                    enable=item.enable,
                    reconciliation_rule=item.reconciliation_rule,
                    description=item.description or ''
                )
                result['created'] += 1
        else:  # merge 模式
            for item in template.items.all():
                # 检查是否已存在相同路径的账户
                existing = Account.objects.filter(owner=user, account=item.account_path).first()

                if existing:
                    if conflict_resolution == 'skip':
                        result['skipped'] += 1
                        continue
                    elif conflict_resolution == 'overwrite':
                        existing.delete()
                        result['overwritten'] += 1

                # 创建新账户（Account.save() 会自动创建父账户）
                Account.objects.create(
                    owner=user,
                    account=item.account_path,
                    enable=item.enable,
                    reconciliation_rule=item.reconciliation_rule,
                    description=item.description or ''
                )
                result['created'] += 1

        return result
