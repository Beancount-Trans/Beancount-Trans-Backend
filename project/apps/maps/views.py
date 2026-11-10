from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from project.apps.maps.models import Expense, Assets, Income, Template, TemplateItem
from project.apps.account.models import Account
from project.apps.common.permissions import TemplatePermission, IsOwnerOrAdminReadWriteOnly
from project.apps.common.views import BaseMappingViewSet
from project.apps.maps.batch_update import BatchUpdateMappingError, batch_update_mapping_accounts
from project.apps.maps.serializers import (
    AssetsSerializer, ExpenseSerializer, IncomeSerializer,
    TemplateItemSerializer, TemplateListSerializer, TemplateDetailSerializer,
    TemplateApplySerializer, ExpenseBatchUpdateSerializer,
    IncomeBatchUpdateSerializer, AssetsBatchUpdateSerializer,
)
from django.shortcuts import get_object_or_404


class ExpenseViewSet(BaseMappingViewSet):
    """支出映射管理视图集"""
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    search_fields = ['key', 'payee']
    ordering_fields = ['id', 'key']

    def get_queryset(self):
        """优化查询，预加载标签"""
        return super().get_queryset().prefetch_related('tags')

    # @action(detail=True, methods=['get'])
    # def tags(self, request, pk=None):
    #     """获取支出映射关联的标签"""
    #     expense = self.get_object()
    #     from project.apps.tags.serializers import TagSerializer
    #     tags = expense.tags.filter(enable=True)
    #     serializer = TagSerializer(tags, many=True, context={'request': request})
    #     return Response({
    #         'expense_id': expense.id,
    #         'expense_key': expense.key,
    #         'tags': serializer.data,
    #         'count': tags.count()
    #     })

    # @action(detail=True, methods=['post'])
    # def add_tags(self, request, pk=None):
    #     """为支出映射添加标签

    #     请求体: {"tag_ids": [1, 2, 3]}
    #     """
    #     if request.user.is_anonymous:
    #         raise PermissionDenied("Permission denied. Please log in.")

    #     expense = self.get_object()
    #     tag_ids = request.data.get('tag_ids', [])

    #     if not tag_ids:
    #         return Response(
    #             {"error": "tag_ids字段不能为空"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     # 验证标签是否存在且属于当前用户
    #     from project.apps.tags.models import Tag
    #     tags = Tag.objects.filter(id__in=tag_ids, owner=request.user, enable=True)
    #     if tags.count() != len(tag_ids):
    #         return Response(
    #             {"error": "部分标签不存在、已禁用或无权限访问"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     # 添加标签（不会重复添加）
    #     expense.tags.add(*tags)

    #     from project.apps.tags.serializers import TagSerializer
    #     all_tags = expense.tags.filter(enable=True)
    #     serializer = TagSerializer(all_tags, many=True, context={'request': request})

    #     return Response({
    #         'message': f'成功添加 {len(tag_ids)} 个标签',
    #         'tags': serializer.data
    #     })

    # @action(detail=True, methods=['post'])
    # def remove_tags(self, request, pk=None):
    #     """从支出映射中移除标签

    #     请求体: {"tag_ids": [1, 2, 3]}
    #     """
    #     if request.user.is_anonymous:
    #         raise PermissionDenied("Permission denied. Please log in.")

    #     expense = self.get_object()
    #     tag_ids = request.data.get('tag_ids', [])

    #     if not tag_ids:
    #         return Response(
    #             {"error": "tag_ids字段不能为空"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     # 移除标签
    #     from project.apps.tags.models import Tag
    #     tags = Tag.objects.filter(id__in=tag_ids, owner=request.user)
    #     expense.tags.remove(*tags)

    #     from project.apps.tags.serializers import TagSerializer
    #     remaining_tags = expense.tags.filter(enable=True)
    #     serializer = TagSerializer(remaining_tags, many=True, context={'request': request})

    #     return Response({
    #         'message': f'成功移除 {len(tag_ids)} 个标签',
    #         'tags': serializer.data
    #     })

    @action(detail=False, methods=['post'])
    def batch_update_account(self, request):
        """批量更新支出映射的账户"""
        if request.user.is_anonymous:
            raise PermissionDenied("Permission denied. Please log in.")

        serializer = ExpenseBatchUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            updated_count = batch_update_mapping_accounts(
                user=request.user,
                model=Expense,
                mapping_ids=data['expense_ids'],
                account_id=data['expend_id'],
                account_fk_name='expend_id',
                mapping_label='支出映射',
            )
        except BatchUpdateMappingError as exc:
            return Response({"error": exc.message}, status=exc.status_code)

        return Response({
            "message": f"成功更新 {updated_count} 个支出映射",
            "updated_count": updated_count
        })


class AssetsViewSet(BaseMappingViewSet):
    """资产映射管理视图集"""
    queryset = Assets.objects.all()
    serializer_class = AssetsSerializer
    search_fields = ['key']
    ordering_fields = ['id', 'key']

    def get_queryset(self):
        """优化查询，预加载标签"""
        return super().get_queryset().prefetch_related('tags')

    # @action(detail=True, methods=['get'])
    # def tags(self, request, pk=None):
    #     """获取资产映射关联的标签"""
    #     asset = self.get_object()
    #     from project.apps.tags.serializers import TagSerializer
    #     tags = asset.tags.filter(enable=True)
    #     serializer = TagSerializer(tags, many=True, context={'request': request})
    #     return Response({
    #         'asset_id': asset.id,
    #         'asset_key': asset.key,
    #         'tags': serializer.data,
    #         'count': tags.count()
    #     })

    # @action(detail=True, methods=['post'])
    # def add_tags(self, request, pk=None):
    #     """为资产映射添加标签"""
    #     if request.user.is_anonymous:
    #         raise PermissionDenied("Permission denied. Please log in.")

    #     asset = self.get_object()
    #     tag_ids = request.data.get('tag_ids', [])

    #     if not tag_ids:
    #         return Response(
    #             {"error": "tag_ids字段不能为空"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     from project.apps.tags.models import Tag
    #     tags = Tag.objects.filter(id__in=tag_ids, owner=request.user, enable=True)
    #     if tags.count() != len(tag_ids):
    #         return Response(
    #             {"error": "部分标签不存在、已禁用或无权限访问"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     asset.tags.add(*tags)

    #     from project.apps.tags.serializers import TagSerializer
    #     all_tags = asset.tags.filter(enable=True)
    #     serializer = TagSerializer(all_tags, many=True, context={'request': request})

    #     return Response({
    #         'message': f'成功添加 {len(tag_ids)} 个标签',
    #         'tags': serializer.data
    #     })

    # @action(detail=True, methods=['post'])
    # def remove_tags(self, request, pk=None):
    #     """从资产映射中移除标签"""
    #     if request.user.is_anonymous:
    #         raise PermissionDenied("Permission denied. Please log in.")

    #     asset = self.get_object()
    #     tag_ids = request.data.get('tag_ids', [])

    #     if not tag_ids:
    #         return Response(
    #             {"error": "tag_ids字段不能为空"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     from project.apps.tags.models import Tag
    #     tags = Tag.objects.filter(id__in=tag_ids, owner=request.user)
    #     asset.tags.remove(*tags)

    #     from project.apps.tags.serializers import TagSerializer
    #     remaining_tags = asset.tags.filter(enable=True)
    #     serializer = TagSerializer(remaining_tags, many=True, context={'request': request})

    #     return Response({
    #         'message': f'成功移除 {len(tag_ids)} 个标签',
    #         'tags': serializer.data
    #     })

    @action(detail=False, methods=['post'])
    def batch_update_account(self, request):
        """批量更新资产映射的账户"""
        if request.user.is_anonymous:
            raise PermissionDenied("Permission denied. Please log in.")

        serializer = AssetsBatchUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            updated_count = batch_update_mapping_accounts(
                user=request.user,
                model=Assets,
                mapping_ids=data['assets_ids'],
                account_id=data['assets_id'],
                account_fk_name='assets_id',
                mapping_label='资产映射',
            )
        except BatchUpdateMappingError as exc:
            return Response({"error": exc.message}, status=exc.status_code)

        return Response({
            "message": f"成功更新 {updated_count} 个资产映射",
            "updated_count": updated_count
        })


class IncomeViewSet(BaseMappingViewSet):
    """收入映射管理视图集"""
    queryset = Income.objects.all()
    serializer_class = IncomeSerializer
    search_fields = ['key']
    ordering_fields = ['id', 'key']

    def get_queryset(self):
        """优化查询，预加载标签"""
        return super().get_queryset().prefetch_related('tags')

    # @action(detail=True, methods=['get'])
    # def tags(self, request, pk=None):
    #     """获取收入映射关联的标签"""
    #     income = self.get_object()
    #     from project.apps.tags.serializers import TagSerializer
    #     tags = income.tags.filter(enable=True)
    #     serializer = TagSerializer(tags, many=True, context={'request': request})
    #     return Response({
    #         'income_id': income.id,
    #         'income_key': income.key,
    #         'tags': serializer.data,
    #         'count': tags.count()
    #     })

    # @action(detail=True, methods=['post'])
    # def add_tags(self, request, pk=None):
    #     """为收入映射添加标签"""
    #     if request.user.is_anonymous:
    #         raise PermissionDenied("Permission denied. Please log in.")

    #     income = self.get_object()
    #     tag_ids = request.data.get('tag_ids', [])

    #     if not tag_ids:
    #         return Response(
    #             {"error": "tag_ids字段不能为空"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     from project.apps.tags.models import Tag
    #     tags = Tag.objects.filter(id__in=tag_ids, owner=request.user, enable=True)
    #     if tags.count() != len(tag_ids):
    #         return Response(
    #             {"error": "部分标签不存在、已禁用或无权限访问"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     income.tags.add(*tags)

    #     from project.apps.tags.serializers import TagSerializer
    #     all_tags = income.tags.filter(enable=True)
    #     serializer = TagSerializer(all_tags, many=True, context={'request': request})

    #     return Response({
    #         'message': f'成功添加 {len(tag_ids)} 个标签',
    #         'tags': serializer.data
    #     })

    # @action(detail=True, methods=['post'])
    # def remove_tags(self, request, pk=None):
    #     """从收入映射中移除标签"""
    #     if request.user.is_anonymous:
    #         raise PermissionDenied("Permission denied. Please log in.")

    #     income = self.get_object()
    #     tag_ids = request.data.get('tag_ids', [])

    #     if not tag_ids:
    #         return Response(
    #             {"error": "tag_ids字段不能为空"},
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     from project.apps.tags.models import Tag
    #     tags = Tag.objects.filter(id__in=tag_ids, owner=request.user)
    #     income.tags.remove(*tags)

    #     from project.apps.tags.serializers import TagSerializer
    #     remaining_tags = income.tags.filter(enable=True)
    #     serializer = TagSerializer(remaining_tags, many=True, context={'request': request})

    #     return Response({
    #         'message': f'成功移除 {len(tag_ids)} 个标签',
    #         'tags': serializer.data
    #     })

    @action(detail=False, methods=['post'])
    def batch_update_account(self, request):
        """批量更新收入映射的账户"""
        if request.user.is_anonymous:
            raise PermissionDenied("Permission denied. Please log in.")

        serializer = IncomeBatchUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            updated_count = batch_update_mapping_accounts(
                user=request.user,
                model=Income,
                mapping_ids=data['income_ids'],
                account_id=data['income_id'],
                account_fk_name='income_id',
                mapping_label='收入映射',
            )
        except BatchUpdateMappingError as exc:
            return Response({"error": exc.message}, status=exc.status_code)

        return Response({
            "message": f"成功更新 {updated_count} 个收入映射",
            "updated_count": updated_count
        })


class TemplateViewSet(ModelViewSet):
    permission_classes = [TemplatePermission]
    # 移除固定的serializer_class，改为动态选择

    def get_serializer_class(self):
        if self.action == 'list':
            return TemplateListSerializer
        elif self.action == 'retrieve':
            return TemplateDetailSerializer
        return TemplateDetailSerializer  # 对于create/update等操作使用DetailSerializer

    def get_queryset(self):
        # 官方模板对所有用户可见
        queryset = Template.objects.filter(is_official=True)

        # 登录用户可以看到自己的模板和公开模板
        if self.request.user.is_authenticated:
            user_templates = Template.objects.filter(owner=self.request.user)
            public_templates = Template.objects.filter(is_public=True, is_official=False)
            queryset = queryset | user_templates | public_templates

        # 按类型过滤
        template_type = self.request.query_params.get('type', None)
        if template_type:
            queryset = queryset.filter(type=template_type)

        return queryset.distinct()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # 对于列表视图，不需要预取items
        if self.action == 'list':
            return context

        # 对于详情视图，预取items以提高性能
        context['queryset'] = Template.objects.prefetch_related('items')

        return context

    def retrieve(self, request, *args, **kwargs):
        # 使用预取优化查询
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


    @action(detail=True, methods=['post'])
    def apply(self, request, pk=None):
        """应用模板到用户映射"""
        template = self.get_object()
        serializer = TemplateApplySerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        action_type = data['action']
        conflict_resolution = data.get('conflict_resolution', 'skip')

        # 根据模板类型应用不同的映射
        if template.type == 'expense':
            result = self._apply_expense_template(template, action_type, conflict_resolution)
        elif template.type == 'income':
            result = self._apply_income_template(template, action_type, conflict_resolution)
        elif template.type == 'assets':
            result = self._apply_assets_template(template, action_type, conflict_resolution)
        else:
            return Response({"error": "未知的模板类型"}, status=status.HTTP_400_BAD_REQUEST)

        # 构建响应消息
        message = "模板应用成功"
        if result.get('missing_accounts'):
            missing_count = len(result['missing_accounts'])
            message += f"。注意：有 {missing_count} 个映射的账户不存在，已设置为空，请及时修改。"

        return Response({
            "message": message,
            "result": result
        })

    def _apply_expense_template(self, template, action_type, conflict_resolution):
        """应用支出模板"""
        from project.apps.account.management.commands.official_templates_loader import (
            load_official_expense_tag_paths_by_key,
            resolve_expense_template_item_tag_paths,
        )
        from project.apps.tags.signals import apply_tags_to_mapping

        json_tag_map = load_official_expense_tag_paths_by_key()
        result = {
            'created': 0,
            'skipped': 0,
            'overwritten': 0,
            'missing_accounts': []
        }

        if action_type == 'overwrite':
            # 删除用户现有的所有支出映射
            Expense.objects.filter(owner=self.request.user).delete()
            # 直接创建所有模板映射，无需检查冲突
            for item in template.items.all():
                # 根据账户路径在目标用户账户中查找
                target_account = None
                if item.account:  # item.account 现在是账户路径字符串
                    target_account = Account.objects.filter(
                        account=item.account,
                        owner=self.request.user
                    ).first()
                    if not target_account:
                        result['missing_accounts'].append({
                            'key': item.key,
                            'account': item.account
                        })

                expense = Expense.objects.create(
                    owner=self.request.user,
                    key=item.key,
                    payee=item.payee,
                    expend=target_account,
                    currency=item.currency
                )
                tag_paths = resolve_expense_template_item_tag_paths(item, json_tag_map)
                apply_tags_to_mapping(expense, self.request.user, tag_paths)
                result['created'] += 1
        else:  # merge 模式
            for item in template.items.all():
                # 检查是否已存在相同关键字的映射
                existing = Expense.objects.filter(owner=self.request.user, key=item.key).first()

                if existing:
                    if conflict_resolution == 'skip':
                        result['skipped'] += 1
                        continue
                    elif conflict_resolution == 'overwrite':
                        existing.delete()
                        result['overwritten'] += 1

                # 根据账户路径在目标用户账户中查找
                target_account = None
                if item.account:  # item.account 现在是账户路径字符串
                    target_account = Account.objects.filter(
                        account=item.account,
                        owner=self.request.user
                    ).first()
                    if not target_account:
                        result['missing_accounts'].append({
                            'key': item.key,
                            'account': item.account
                        })

                # 创建新的映射
                expense = Expense.objects.create(
                    owner=self.request.user,
                    key=item.key,
                    payee=item.payee,
                    expend=target_account,
                    currency=item.currency
                )
                tag_paths = resolve_expense_template_item_tag_paths(item, json_tag_map)
                apply_tags_to_mapping(expense, self.request.user, tag_paths)
                result['created'] += 1

        return result

    def _apply_income_template(self, template, action_type, conflict_resolution):
        """应用收入模板"""
        result = {
            'created': 0,
            'skipped': 0,
            'overwritten': 0,
            'missing_accounts': []
        }

        if action_type == 'overwrite':
            # 删除用户现有的所有收入映射
            Income.objects.filter(owner=self.request.user).delete()
            # 直接创建所有模板映射，无需检查冲突
            for item in template.items.all():
                # 根据账户路径在目标用户账户中查找
                target_account = None
                if item.account:  # item.account 现在是账户路径字符串
                    target_account = Account.objects.filter(
                        account=item.account,
                        owner=self.request.user
                    ).first()
                    if not target_account:
                        result['missing_accounts'].append({
                            'key': item.key,
                            'account': item.account
                        })

                Income.objects.create(
                    owner=self.request.user,
                    key=item.key,
                    payer=item.payer,
                    income=target_account
                )
                result['created'] += 1
        else:  # merge 模式
            for item in template.items.all():
                # 检查是否已存在相同关键字的映射
                existing = Income.objects.filter(owner=self.request.user, key=item.key).first()

                if existing:
                    if conflict_resolution == 'skip':
                        result['skipped'] += 1
                        continue
                    elif conflict_resolution == 'overwrite':
                        existing.delete()
                        result['overwritten'] += 1

                # 根据账户路径在目标用户账户中查找
                target_account = None
                if item.account:  # item.account 现在是账户路径字符串
                    target_account = Account.objects.filter(
                        account=item.account,
                        owner=self.request.user
                    ).first()
                    if not target_account:
                        result['missing_accounts'].append({
                            'key': item.key,
                            'account': item.account
                        })

                # 创建新的映射
                Income.objects.create(
                    owner=self.request.user,
                    key=item.key,
                    payer=item.payer,
                    income=target_account
                )
                result['created'] += 1

        return result

    def _apply_assets_template(self, template, action_type, conflict_resolution):
        """应用资产模板"""
        result = {
            'created': 0,
            'skipped': 0,
            'overwritten': 0,
            'missing_accounts': []
        }

        if action_type == 'overwrite':
            # 删除用户现有的所有资产映射
            Assets.objects.filter(owner=self.request.user).delete()
            # 直接创建所有模板映射，无需检查冲突
            for item in template.items.all():
                # 根据账户路径在目标用户账户中查找
                target_account = None
                if item.account:  # item.account 现在是账户路径字符串
                    target_account = Account.objects.filter(
                        account=item.account,
                        owner=self.request.user
                    ).first()
                    if not target_account:
                        result['missing_accounts'].append({
                            'key': item.key,
                            'account': item.account
                        })

                Assets.objects.create(
                    owner=self.request.user,
                    key=item.key,
                    assets=target_account
                )
                result['created'] += 1
        else:  # merge 模式
            for item in template.items.all():
                # 检查是否已存在相同关键字的映射
                existing = Assets.objects.filter(owner=self.request.user, key=item.key).first()

                if existing:
                    if conflict_resolution == 'skip':
                        result['skipped'] += 1
                        continue
                    elif conflict_resolution == 'overwrite':
                        existing.delete()
                        result['overwritten'] += 1

                # 根据账户路径在目标用户账户中查找
                target_account = None
                if item.account:  # item.account 现在是账户路径字符串
                    target_account = Account.objects.filter(
                        account=item.account,
                        owner=self.request.user
                    ).first()
                    if not target_account:
                        result['missing_accounts'].append({
                            'key': item.key,
                            'account': item.account
                        })

                # 创建新的映射
                Assets.objects.create(
                    owner=self.request.user,
                    key=item.key,
                    assets=target_account
                )
                result['created'] += 1

        return result

class TemplateItemViewSet(ModelViewSet):
    """模板项管理视图集"""
    serializer_class = TemplateItemSerializer
    permission_classes = [IsOwnerOrAdminReadWriteOnly]

    def get_queryset(self):
        """获取模板项查询集"""
        User = get_user_model()
        if self.request.user.is_authenticated:
            return TemplateItem.objects.filter(template__owner=self.request.user)
        else:
            # 匿名用户使用id=1用户的模板项
            try:
                default_user = User.objects.get(id=1)
                return TemplateItem.objects.filter(template__owner=default_user)
            except User.DoesNotExist:
                return TemplateItem.objects.none()

    def perform_create(self, serializer):
        """创建模板项"""
        if self.request.user.is_anonymous:
            raise PermissionDenied("Permission denied. Please log in.")

        template_id = self.kwargs.get('template_pk')
        template = get_object_or_404(Template, pk=template_id, owner=self.request.user)
        serializer.save(template=template)
