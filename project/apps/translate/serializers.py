# project/apps/translate/serializers.py
from rest_framework import serializers
from project.apps.translate.models import FormatConfig


class AnalyzeSerializer(serializers.Serializer):
    cmb_credit_ignore = serializers.BooleanField(required=False, default=False)
    boc_debit_ignore = serializers.BooleanField(required=False, default=False)
    # 关闭支付宝/微信默认忽略（交易关闭、余额宝收益等），解析全部条目
    no_ignore = serializers.BooleanField(required=False, default=False)
    write = serializers.BooleanField(required=False, default=False)
    password = serializers.CharField(required=False, allow_blank=True)
    passwords = serializers.DictField(child=serializers.CharField(allow_blank=True), required=False)
    csrfmiddlewaretoken = serializers.CharField(required=False, allow_blank=False)
    # 加密类型：auto（按扩展名自动识别）、pdf_password、zip_password、none
    encryption_type = serializers.CharField(required=False, allow_blank=True, default='auto')
    # 解析首页可选的解析引擎；不传时按用户配置（默认 BERT）
    parse_engine = serializers.ChoiceField(
        choices=['BERT', 'Platform'], required=False, allow_blank=True
    )


class FormatConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormatConfig
        fields = '__all__'
        read_only_fields = ('owner', 'id')  # 禁止修改所属用户

    def validate_flag(self, value):
        """验证标记符号有效性"""
        allowed_flags = ['*', '!', '#']
        if value not in allowed_flags:
            raise serializers.ValidationError(
                f"标记符号只能是 {', '.join(allowed_flags)} 中的一个"
            )
        return value

    def validate_ai_model(self, value):
        """归一化解析引擎；spaCy 已移除，空值与平台 LLM 归一为本地 BERT。"""
        if not value:
            return 'BERT'
        if value.lower() == 'spacy':
            return 'BERT'
        if value.lower() == 'bert':
            return 'BERT'
        # 平台 LLM 仅作为解析首页的按次参数，不写入用户配置
        if value.lower() == 'platform':
            return 'BERT'
        if value not in {'DeepSeek', 'None'}:
            raise serializers.ValidationError('不支持的解析引擎')
        return value

    def validate(self, attrs):
        ai_model = attrs.get('ai_model', getattr(self.instance, 'ai_model', None))
        assistant_key = attrs.get(
            'assistant_api_key',
            getattr(self.instance, 'assistant_api_key', None) if self.instance else None,
        )
        if ai_model == 'DeepSeek' and not (assistant_key or '').strip():
            raise serializers.ValidationError({
                'ai_model': '自有 DeepSeek 解析需要先在 BYOK 填写 API 密钥',
            })
        return attrs

    def create(self, validated_data):
        """创建时自动关联当前用户"""
        user = self.context['request'].user
        assistant_key = (validated_data.get('assistant_api_key') or '').strip() or None
        if 'assistant_api_key' in validated_data:
            validated_data['deepseek_apikey'] = assistant_key
        return FormatConfig.objects.create(owner=user, **validated_data)

    def update(self, instance, validated_data):
        if 'assistant_api_key' in validated_data:
            validated_data['deepseek_apikey'] = (
                (validated_data.get('assistant_api_key') or '').strip() or None
            )
        return super().update(instance, validated_data)


# class ReparseSerializer(serializers.Serializer):
#     """解析器配置序列化器"""
#     ai_model = serializers.CharField(required=False, allow_blank=True, default='deepseek-chat')
#     deepseek_apikey = serializers.CharField(required=False, allow_blank=True, default='')
#     flag = serializers.CharField(required=False, allow_blank=True, default='*')
#     selected_key = serializers.CharField(required=False, allow_blank=True, default=None)
#     enable_realtime = serializers.BooleanField(required=False, default=True)

#     def validate(self, attrs):
#         """验证配置"""
#         if not attrs.get('ai_model'):
#             raise serializers.ValidationError("AI模型不能为空")
#         return attrs

class ReparseSerializer(serializers.Serializer):
    """解析器配置序列化器"""
    entry_id = serializers.CharField(required=True)
    selected_key = serializers.CharField(required=True)
    mapping_type = serializers.ChoiceField(
        choices=['expense', 'income', 'asset'],
        required=False,
        default='expense',
    )