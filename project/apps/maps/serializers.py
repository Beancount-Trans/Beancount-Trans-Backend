from rest_framework import serializers
from project.apps.maps.models import Expense, Assets, Income, Template, TemplateItem
from project.apps.account.models import Account
from project.apps.tags.models import Tag


class AccountSummarySerializer(serializers.ModelSerializer):
    """账户摘要序列化器，用于在映射中显示账户信息"""
    class Meta:
        model = Account
        fields = ['id', 'account', 'enable']


class TagSummarySerializer(serializers.ModelSerializer):
    """标签摘要序列化器，用于在映射中显示标签信息"""
    full_path = serializers.CharField(source='get_full_path', read_only=True)

    class Meta:
        model = Tag
        fields = ['id', 'name', 'full_path', 'enable']


class ExpenseSerializer(serializers.ModelSerializer):
    payee = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    # 读取时显示详细信息
    expend = AccountSummarySerializer(read_only=True)
    tags = TagSummarySerializer(many=True, read_only=True)

    # 写入时使用ID
    expend_id = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        source='expend',
        write_only=True,
        required=False,
        allow_null=True
    )
    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        many=True,
        write_only=True,
        required=False,
        help_text="标签ID列表"
    )
    expend_account = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=128,
        help_text="映射账户路径（与 expend_id 二选一，优先 expend_id）",
    )

    owner = serializers.ReadOnlyField(source='owner.username')
    enable = serializers.BooleanField(default=True, help_text="是否启用", required=False)

    class Meta:
        model = Expense
        fields = [
            'id', 'owner', 'key', 'payee', 'expend', 'expend_id', 'expend_account',
            'currency', 'enable', 'tags', 'tag_ids',
        ]
        extra_kwargs = {
            'key': {'required': False}  # 允许更新时不传 key
        }

    def validate(self, attrs):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return attrs
        user = request.user

        path_raw = attrs.pop('expend_account', None)
        path = (path_raw or '').strip()
        expend = attrs.get('expend')

        if expend is not None:
            return attrs
        if path:
            acc = Account.objects.filter(account=path, owner=user).first()
            attrs['expend'] = acc if acc else None
        return attrs

    def create(self, validated_data):
        """创建支出映射，处理标签关联"""
        tag_ids = validated_data.pop('tag_ids', [])
        expense = Expense.objects.create(**validated_data)
        if tag_ids:
            expense.tags.set(tag_ids)
        return expense

    def update(self, instance, validated_data):
        """更新支出映射，处理标签关联"""
        tag_ids = validated_data.pop('tag_ids', None)

        # 更新基本字段
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # 更新标签关联（如果提供了tag_ids）
        if tag_ids is not None:
            instance.tags.set(tag_ids)

        return instance


class AssetsSerializer(serializers.ModelSerializer):
    # 读取时显示详细信息
    assets = AccountSummarySerializer(read_only=True)
    tags = TagSummarySerializer(many=True, read_only=True)

    # 写入时使用ID
    assets_id = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        source='assets',
        write_only=True,
        required=False,
        allow_null=True
    )
    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        many=True,
        write_only=True,
        required=False,
        help_text="标签ID列表"
    )
    assets_account = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=128,
        help_text="映射账户路径（与 assets_id 二选一，优先 assets_id）",
    )

    owner = serializers.ReadOnlyField(source='owner.username')
    enable = serializers.BooleanField(default=True, help_text="是否启用", required=False)

    class Meta:
        model = Assets
        fields = [
            'id', 'owner', 'key', 'assets', 'assets_id', 'assets_account',
            'enable', 'tags', 'tag_ids',
        ]

    def validate(self, attrs):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return attrs
        user = request.user

        path_raw = attrs.pop('assets_account', None)
        path = (path_raw or '').strip()
        assets = attrs.get('assets')

        if assets is not None:
            return attrs
        if path:
            acc = Account.objects.filter(account=path, owner=user).first()
            attrs['assets'] = acc if acc else None
        return attrs

    def create(self, validated_data):
        """创建资产映射，处理标签关联"""
        tag_ids = validated_data.pop('tag_ids', [])
        asset = Assets.objects.create(**validated_data)
        if tag_ids:
            asset.tags.set(tag_ids)
        return asset

    def update(self, instance, validated_data):
        """更新资产映射，处理标签关联"""
        tag_ids = validated_data.pop('tag_ids', None)

        # 更新基本字段
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # 更新标签关联（如果提供了tag_ids）
        if tag_ids is not None:
            instance.tags.set(tag_ids)

        return instance


class IncomeSerializer(serializers.ModelSerializer):
    # 读取时显示详细信息
    income = AccountSummarySerializer(read_only=True)
    tags = TagSummarySerializer(many=True, read_only=True)

    # 写入时使用ID
    income_id = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        source='income',
        write_only=True,
        required=False,
        allow_null=True
    )
    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        many=True,
        write_only=True,
        required=False,
        help_text="标签ID列表"
    )
    income_account = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=128,
        help_text="映射账户路径（与 income_id 二选一，优先 income_id）",
    )

    owner = serializers.ReadOnlyField(source='owner.username')
    enable = serializers.BooleanField(default=True, help_text="是否启用", required=False)

    class Meta:
        model = Income
        fields = [
            'id', 'owner', 'key', 'payer', 'income', 'income_id', 'income_account',
            'enable', 'tags', 'tag_ids',
        ]

    def validate(self, attrs):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return attrs
        user = request.user

        path_raw = attrs.pop('income_account', None)
        path = (path_raw or '').strip()
        income = attrs.get('income')

        if income is not None:
            return attrs
        if path:
            acc = Account.objects.filter(account=path, owner=user).first()
            attrs['income'] = acc if acc else None
        return attrs

    def create(self, validated_data):
        """创建收入映射，处理标签关联"""
        tag_ids = validated_data.pop('tag_ids', [])
        income = Income.objects.create(**validated_data)
        if tag_ids:
            income.tags.set(tag_ids)
        return income

    def update(self, instance, validated_data):
        """更新收入映射，处理标签关联"""
        tag_ids = validated_data.pop('tag_ids', None)

        # 更新基本字段
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # 更新标签关联（如果提供了tag_ids）
        if tag_ids is not None:
            instance.tags.set(tag_ids)

        return instance


class TemplateItemSerializer(serializers.ModelSerializer):
    # account 字段现在是账户路径字符串，不再是外键
    account = serializers.CharField(
        max_length=128,
        allow_null=True,
        allow_blank=True,
        required=False,
        help_text="映射账户"
    )

    class Meta:
        model = TemplateItem
        fields = '__all__'
        read_only_fields = ('created', 'modified')


class TemplateListSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.username', read_only=True)

    class Meta:
        model = Template
        fields = ['id', 'name', 'description', 'type', 'is_public', 'is_official', 
                 'version', 'update_notes', 'owner', 'owner_name', 'created', 'modified']
        read_only_fields = ('created', 'modified', 'owner')


class TemplateDetailSerializer(serializers.ModelSerializer):
    items = TemplateItemSerializer(many=True, required=False)
    owner_name = serializers.CharField(source='owner.username', read_only=True)

    class Meta:
        model = Template
        fields = '__all__'
        read_only_fields = ('created', 'modified', 'owner')

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        template = Template.objects.create(**validated_data)
        for item_data in items_data:
            TemplateItem.objects.create(template=template, **item_data)
        return template

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])

        # 更新模板基本信息
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # 处理模板项 - 先删除所有现有项，然后重新创建
        instance.items.all().delete()
        for item_data in items_data:
            TemplateItem.objects.create(template=instance, **item_data)

        return instance


class ExpenseBatchUpdateSerializer(serializers.Serializer):
    """支出映射批量更新账户序列化器"""
    expense_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="要更新的支出映射ID列表"
    )
    expend_id = serializers.IntegerField(
        help_text="新的支出账户ID"
    )

    def validate_expense_ids(self, value):
        if not value:
            raise serializers.ValidationError("支出映射ID列表不能为空")
        return value


class IncomeBatchUpdateSerializer(serializers.Serializer):
    """收入映射批量更新账户序列化器"""
    income_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="要更新的收入映射ID列表"
    )
    income_id = serializers.IntegerField(
        help_text="新的收入账户ID"
    )

    def validate_income_ids(self, value):
        if not value:
            raise serializers.ValidationError("收入映射ID列表不能为空")
        return value


class AssetsBatchUpdateSerializer(serializers.Serializer):
    """资产映射批量更新账户序列化器"""
    assets_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="要更新的资产映射ID列表"
    )
    assets_id = serializers.IntegerField(
        help_text="新的资产账户ID"
    )

    def validate_assets_ids(self, value):
        if not value:
            raise serializers.ValidationError("资产映射ID列表不能为空")
        return value


class TemplateApplySerializer(serializers.Serializer):
    """映射模板应用序列化器

    注意：template_id 不需要传递，因为模板 ID 已在 URL 中
    应用模板的操作不直接对应模型的创建或更新，而是触发一个动作（应用模板到用户映射）
    """
    action = serializers.ChoiceField(choices=['overwrite', 'merge'])
    conflict_resolution = serializers.ChoiceField(
        choices=['skip', 'overwrite'], 
        required=False,
        default='skip'
    )
