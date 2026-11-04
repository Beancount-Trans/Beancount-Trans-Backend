from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import serializers
# from django.contrib.auth.models import User
from project.apps.account.currency import validate_account_currency
from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem

_DUPLICATE_ACCOUNT_MSG = '该账户路径已存在，请勿重复添加'


class AccountTreeSerializer(serializers.ModelSerializer):
    """账户树形结构序列化器"""
    children = serializers.SerializerMethodField()
    parent_account = serializers.CharField(source='parent.account', read_only=True)
    account_type = serializers.CharField(source='get_account_type', read_only=True)
    mapping_count = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            'id', 'account', 'parent', 'parent_account',
            'owner', 'enable', 'currency', 'account_type', 'mapping_count',
            'reconciliation_rule',
            'description', 'children', 'created', 'modified'
        ]
        read_only_fields = ['id', 'created', 'modified', 'owner']

    def get_children(self, obj):
        """递归获取子账户"""
        children = obj.children.all().order_by('account')
        if children.exists():
            return AccountTreeSerializer(children, many=True, context=self.context).data
        return []

    def get_mapping_count(self, obj):
        """获取与此账户相关的映射数量"""
        from django.apps import apps

        try:
            Expense = apps.get_model('maps', 'Expense')
            Assets = apps.get_model('maps', 'Assets')
            Income = apps.get_model('maps', 'Income')

            # 获取当前用户，匿名用户使用id=1用户的数据
            request = self.context.get('request')
            if not request:
                return {'expense': 0, 'assets': 0, 'income': 0, 'total': 0}

            from django.contrib.auth import get_user_model
            User = get_user_model()

            if request.user.is_authenticated:
                user = request.user
            else:
                # 匿名用户使用id=1用户的映射
                try:
                    user = User.objects.get(id=1)
                except User.DoesNotExist:
                    return {'expense': 0, 'assets': 0, 'income': 0, 'total': 0}

            # 统计用户的所有映射记录（包括已关闭的）
            expense_count = Expense.objects.filter(expend=obj, owner=user).count()
            assets_count = Assets.objects.filter(assets=obj, owner=user).count()
            income_count = Income.objects.filter(income=obj, owner=user).count()

            return {
                'expense': expense_count,
                'assets': assets_count,
                'income': income_count,
                'total': expense_count + assets_count + income_count
            }
        except:
            return {'expense': 0, 'assets': 0, 'income': 0, 'total': 0}


class AccountSerializer(serializers.ModelSerializer):
    """账户基础序列化器"""
    parent_account = serializers.CharField(source='parent.account', read_only=True)
    account_type = serializers.CharField(source='get_account_type', read_only=True)
    has_children = serializers.BooleanField(read_only=True)
    mapping_count = serializers.SerializerMethodField()
    ledger_sync_warning = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            'id', 'account', 'parent', 'parent_account',
            'owner', 'enable', 'currency', 'account_type', 'has_children', 'mapping_count',
            'reconciliation_rule',
            'description', 'ledger_sync_warning', 'created', 'modified'
        ]
        read_only_fields = ['id', 'created', 'modified', 'owner', 'ledger_sync_warning']

    def get_ledger_sync_warning(self, obj):
        return getattr(obj, 'ledger_sync_warning', None)

    def validate_reconciliation_rule(self, value):
        """校验对账周期规则（受控自然语言 DSL）"""
        if value:
            from project.apps.reconciliation.services.recurrence import (
                RecurrenceParser,
                RecurrenceParseError,
            )
            try:
                RecurrenceParser.parse(value)
            except RecurrenceParseError as e:
                raise serializers.ValidationError(str(e))
        return value

    def get_mapping_count(self, obj):
        """获取与此账户相关的映射数量"""
        from django.apps import apps

        try:
            Expense = apps.get_model('maps', 'Expense')
            Assets = apps.get_model('maps', 'Assets')
            Income = apps.get_model('maps', 'Income')

            # 获取当前用户，匿名用户使用id=1用户的数据
            request = self.context.get('request')
            if not request:
                return {'expense': 0, 'assets': 0, 'income': 0, 'total': 0}

            from django.contrib.auth import get_user_model
            User = get_user_model()

            if request.user.is_authenticated:
                user = request.user
            else:
                # 匿名用户使用id=1用户的映射
                try:
                    user = User.objects.get(id=1)
                except User.DoesNotExist:
                    return {'expense': 0, 'assets': 0, 'income': 0, 'total': 0}

            # 统计用户的所有映射记录（包括已关闭的）
            expense_count = Expense.objects.filter(expend=obj, owner=user).count()
            assets_count = Assets.objects.filter(assets=obj, owner=user).count()
            income_count = Income.objects.filter(income=obj, owner=user).count()

            return {
                'expense': expense_count,
                'assets': assets_count,
                'income': income_count,
                'total': expense_count + assets_count + income_count
            }
        except:
            return {'expense': 0, 'assets': 0, 'income': 0, 'total': 0}

    def validate(self, attrs):
        """校验币种格式、账户类型与账本历史交易冲突"""
        final_account = attrs.get('account', getattr(self.instance, 'account', ''))
        raw_currency = attrs.get('currency', getattr(self.instance, 'currency', ''))

        scan_conflicts = bool(raw_currency) and self.instance is None
        if self.instance is not None:
            scan_conflicts = (raw_currency or '') != (self.instance.currency or '')

        request = self.context.get('request')
        user = None
        if request and getattr(request.user, 'is_authenticated', False):
            user = request.user

        try:
            attrs['currency'] = validate_account_currency(
                final_account,
                raw_currency,
                user,
                scan_conflicts=scan_conflicts,
            )
        except DjangoValidationError as e:
            raise serializers.ValidationError({'currency': list(e.messages)})

        if self.instance is not None:
            # 标记已校验，避免模型 save() 重复扫描账本
            self.instance._currency_validated = True

        return attrs

    def validate_account(self, value):
        """验证账户路径格式"""
        from project.apps.account.models import _is_valid_account_part
        
        if not all(_is_valid_account_part(part) for part in value.split(':')):
            raise serializers.ValidationError("账户路径必须由字母、数字和连字符组成，用冒号分隔，不能以连字符开头或结尾")

        # 验证根账户类型
        root = value.split(':')[0]
        valid_roots = ['Assets', 'Liabilities', 'Equity', 'Income', 'Expenses']
        if root not in valid_roots:
            raise serializers.ValidationError(f"根账户必须是以下之一: {', '.join(valid_roots)}")

        request = self.context.get('request')
        if request and getattr(request.user, 'is_authenticated', False):
            qs = Account.objects.filter(account=value, owner=request.user)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(_DUPLICATE_ACCOUNT_MSG)

        return value

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError({'account': [_DUPLICATE_ACCOUNT_MSG]})
        except DjangoValidationError as e:
            raise serializers.ValidationError({'currency': list(e.messages)})

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except IntegrityError:
            raise serializers.ValidationError({'account': [_DUPLICATE_ACCOUNT_MSG]})
        except DjangoValidationError as e:
            raise serializers.ValidationError({'currency': list(e.messages)})


class AccountBatchUpdateSerializer(serializers.Serializer):
    """账户批量更新序列化器"""
    account_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="要更新的账户ID列表"
    )
    action = serializers.ChoiceField(
        choices=['enable', 'disable', 'migrate', 'close'],
        help_text="操作类型"
    )
    target_account_id = serializers.IntegerField(
        required=False,
        help_text="目标账户ID（用于迁移操作）"
    )

    def validate_account_ids(self, value):
        """验证账户ID列表"""
        if not value:
            raise serializers.ValidationError("账户ID列表不能为空")
        return value

    def validate(self, data):
        """验证整体数据"""
        action = data.get('action')
        target_account_id = data.get('target_account_id')

        if action == 'migrate' and not target_account_id:
            raise serializers.ValidationError("迁移操作需要指定目标账户ID")

        return data


class AccountMigrationSerializer(serializers.Serializer):
    """账户迁移序列化器"""
    source_account_id = serializers.IntegerField(help_text="源账户ID")
    target_account_id = serializers.IntegerField(help_text="目标账户ID")
    migrate_mappings = serializers.BooleanField(
        default=True,
        help_text="是否迁移相关映射"
    )
    close_source = serializers.BooleanField(
        default=False,
        help_text="是否关闭源账户"
    )

    def validate_source_account_id(self, value):
        """验证源账户"""
        try:
            account = Account.objects.get(id=value)
            if not account.enable:
                raise serializers.ValidationError("源账户已禁用")
            return value
        except Account.DoesNotExist:
            raise serializers.ValidationError("源账户不存在")

    def validate_target_account_id(self, value):
        """验证目标账户"""
        try:
            account = Account.objects.get(id=value)
            if not account.enable:
                raise serializers.ValidationError("目标账户已禁用")
            return value
        except Account.DoesNotExist:
            raise serializers.ValidationError("目标账户不存在")

    def validate(self, data):
        """验证整体数据"""
        source_id = data.get('source_account_id')
        target_id = data.get('target_account_id')

        if source_id == target_id:
            raise serializers.ValidationError("源账户和目标账户不能相同")

        return data


class AccountDeleteSerializer(serializers.Serializer):
    """账户删除序列化器"""
    migrate_to = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="迁移目标账户ID（可选，无映射时可为空）"
    )

    def validate_migrate_to(self, value):
        """验证迁移目标账户"""
        if value is None:
            return value

        try:
            account = Account.objects.get(id=value)
            if not account.enable:
                raise serializers.ValidationError("目标账户已禁用")
            return value
        except Account.DoesNotExist:
            raise serializers.ValidationError("目标账户不存在")


class AccountTemplateItemSerializer(serializers.ModelSerializer):
    """账户模板项序列化器"""
    class Meta:
        model = AccountTemplateItem
        fields = ['id', 'account_path', 'enable', 'reconciliation_rule', 'description', 'created', 'modified']
        read_only_fields = ['created', 'modified']

    def validate_reconciliation_rule(self, value):
        """校验对账周期规则（受控自然语言 DSL）"""
        if value:
            from project.apps.reconciliation.services.recurrence import (
                RecurrenceParser,
                RecurrenceParseError,
            )
            try:
                RecurrenceParser.parse(value)
            except RecurrenceParseError as e:
                raise serializers.ValidationError(str(e))
        return value


class AccountTemplateListSerializer(serializers.ModelSerializer):
    """账户模板列表序列化器"""
    owner_name = serializers.CharField(source='owner.username', read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = AccountTemplate
        fields = ['id', 'name', 'description', 'is_public', 'is_official', 
                 'version', 'update_notes', 'owner', 'owner_name', 'items_count',
                 'created', 'modified']
        read_only_fields = ['created', 'modified', 'owner']

    def get_items_count(self, obj):
        """获取模板项数量"""
        return obj.items.count()


class AccountTemplateDetailSerializer(serializers.ModelSerializer):
    """账户模板详情序列化器（含模板项）"""
    items = AccountTemplateItemSerializer(many=True, required=False)
    owner_name = serializers.CharField(source='owner.username', read_only=True)

    class Meta:
        model = AccountTemplate
        fields = '__all__'
        read_only_fields = ['created', 'modified', 'owner']

    def create(self, validated_data):
        """创建模板及其项"""
        from project.apps.account.models import AccountTemplate, AccountTemplateItem

        items_data = validated_data.pop('items', [])
        template = AccountTemplate.objects.create(**validated_data)

        for item_data in items_data:
            AccountTemplateItem.objects.create(template=template, **item_data)

        return template

    def update(self, instance, validated_data):
        """更新模板及其项"""
        from project.apps.account.models import AccountTemplateItem

        items_data = validated_data.pop('items', [])

        # 更新模板基本信息
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # 处理模板项 - 先删除所有现有项，然后重新创建
        instance.items.all().delete()
        for item_data in items_data:
            AccountTemplateItem.objects.create(template=instance, **item_data)

        return instance


class AccountTemplateApplySerializer(serializers.Serializer):
    """账户模板应用序列化器

    注意：template_id 不需要传递，因为模板 ID 已在 URL 中
    """
    action = serializers.ChoiceField(
        choices=['overwrite', 'merge'],
        help_text="应用方式：overwrite=覆盖所有账户，merge=合并到现有账户"
    )
    conflict_resolution = serializers.ChoiceField(
        choices=['skip', 'overwrite'],
        required=False,
        default='skip',
        help_text="冲突解决策略：skip=跳过冲突项，overwrite=覆盖冲突项"
    )
