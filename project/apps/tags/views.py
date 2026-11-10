from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.db import transaction
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from django.apps import apps

from project.apps.tags.models import Tag
from project.apps.tags.serializers import (
    TagSerializer, 
    TagTreeSerializer,
    TagDeleteSerializer
)
from project.apps.tags.filters import TagFilter
from project.apps.common.permissions import IsOwnerOrAdminReadWriteOnly, AnonymousReadOnlyPermission
from project.apps.common.filters import CurrentUserFilterBackend, AnonymousUserFilterBackend


class TagViewSet(ModelViewSet):
    """
    标签管理视图集

    提供标签管理功能，包括：
    - 标签的增删改查
    - 树形结构展示
    - 批量操作

    支持匿名用户访问id=1用户的数据（只读）
    """
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    permission_classes = [AnonymousReadOnlyPermission]
    filter_backends = [AnonymousUserFilterBackend, DjangoFilterBackend]
    filterset_class = TagFilter
    authentication_classes = [JWTAuthentication]
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created', 'modified']
    ordering = ['name']

    def get_serializer_class(self):
        """根据操作类型返回不同的序列化器"""
        if self.action == 'tree':
            return TagTreeSerializer
        return TagSerializer

    def get_queryset(self):
        """获取查询集"""
        queryset = super().get_queryset()

        # 根据用户过滤，匿名用户使用id=1用户的数据
        User = get_user_model()
        if self.request.user.is_authenticated:
            queryset = queryset.filter(owner=self.request.user)
        else:
            # 匿名用户使用id=1用户的标签
            try:
                default_user = User.objects.get(id=1)
                queryset = queryset.filter(owner=default_user)
            except User.DoesNotExist:
                queryset = queryset.none()

        # 根据启用状态过滤
        enable = self.request.query_params.get('enable')
        if enable is not None:
            queryset = queryset.filter(enable=enable.lower() == 'true')

        return queryset.select_related('parent', 'owner')

    def perform_create(self, serializer):
        """创建标签时设置属主"""
        try:
            serializer.save(owner=self.request.user)
        except DjangoValidationError as e:
            raise ValidationError({'detail': str(e)})

    def perform_update(self, serializer):
        """更新标签时进行验证"""
        try:
            serializer.save()
        except DjangoValidationError as e:
            raise ValidationError({'detail': str(e)})

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """
        获取标签树形结构

        返回所有根标签及其子标签的树形结构
        """
        root_tags = self.get_queryset().filter(parent__isnull=True)

        serializer = self.get_serializer(root_tags, many=True)
        return Response(serializer.data)



    def destroy(self, request, pk=None):
        """
        删除标签

        请求体（可选）:
        {
            "force": false  # 是否强制删除（包括子标签）
        }
        """
        tag = self.get_object()

        # 验证请求数据
        serializer = TagDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        force = serializer.validated_data.get('force', False)

        # 检查是否有子标签
        if tag.has_children() and not force:
            return Response(
                {
                    'error': '标签存在子标签，无法删除',
                    'hint': '请先删除子标签，或使用 force=true 强制删除',
                    'children_count': tag.children.count()
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                result = tag.delete_with_children(force=force)
                return Response({
                    'message': '标签删除成功',
                    'result': result
                })
        except DjangoValidationError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': f'删除标签失败: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def mappings(self, request, pk=None):
        """获取标签相关的映射"""
        tag = self.get_object()

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

            # 通过多对多关系获取包含该标签的映射
            expense_mappings = Expense.objects.filter(
                tags=tag, 
                owner=user
            )
            assets_mappings = Assets.objects.filter(
                tags=tag, 
                owner=user
            )
            income_mappings = Income.objects.filter(
                tags=tag, 
                owner=user
            )

            result = {
                'tag': tag.get_full_path(),
                'tag_id': tag.id,
                'expense_mappings': [
                    {
                        'id': m.id, 
                        'key': m.key, 
                        'payee': m.payee,
                        'account': m.expend.account if m.expend else None,
                        'currency': m.currency,
                        'enable': m.enable
                    }
                    for m in expense_mappings
                ],
                'assets_mappings': [
                    {
                        'id': m.id, 
                        'key': m.key, 
                        'account': m.assets.account if m.assets else None,
                        'enable': m.enable
                    }
                    for m in assets_mappings
                ],
                'income_mappings': [
                    {
                        'id': m.id, 
                        'key': m.key, 
                        'payer': m.payer,
                        'account': m.income.account if m.income else None,
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

