# project/apps/account/management/commands/init_official_templates.py
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem
from project.apps.account.management.commands.official_templates_loader import (
    load_official_account_data,
    load_official_mapping_data,
    load_official_tag_data,
    normalize_mapping_tag_paths,
)
from project.apps.maps.models import Template, TemplateItem
from project.apps.tags.models import Tag, TagTemplate, TagTemplateItem
from project.apps.translate.models import FormatConfig

User = get_user_model()

# 官方模板仅使用 project/fixtures/official_templates/*.json 单一数据源，无内嵌回退


class Command(BaseCommand):
    help = '初始化官方模板和默认用户（id=1）的完整数据'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-admin',
            action='store_true',
            help='跳过创建admin用户（如果已存在）',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制重新创建模板（删除现有官方模板）',
        )

    def handle(self, *args, **options):
        skip_admin = options.get('skip_admin', False)
        force = options.get('force', False)

        self.stdout.write(self.style.SUCCESS('开始初始化官方模板和默认用户...'))

        # 1. 检查并创建 id=1 的 admin 用户
        admin_user = self._ensure_admin_user(skip_admin)
        if not admin_user:
            return

        # 2. 创建官方账户模板
        self._create_official_account_template(admin_user, force)

        # 3. 应用官方账户模板到 admin 用户
        self._apply_account_templates_to_admin(admin_user)

        # 4. 创建官方标签模板
        self._create_official_tag_template(admin_user, force)

        # 5. 应用官方标签模板到 admin 用户
        self._apply_tag_templates_to_admin(admin_user)

        # 6. 创建官方映射模板
        self._create_official_mapping_templates(admin_user, force)

        # 6.1 同步支出映射模板项的标签路径（无需 --force）
        self._sync_official_expense_template_tag_paths()

        # 7. 应用官方映射模板到 admin 用户
        self._apply_mapping_templates_to_admin(admin_user)

        # 8. 确保 admin 用户有格式化配置
        self._ensure_format_config(admin_user)

        # 9. 为 admin 用户创建案例文件（始终按 fixtures 强制覆盖，与 --force 无关）
        self._create_sample_files_for_admin(admin_user)

        self.stdout.write(self.style.SUCCESS('✓ 官方模板和默认用户初始化完成'))

    def _ensure_admin_user(self, skip_admin):
        """确保 id=1 的 admin 用户存在"""
        try:
            admin_user = User.objects.get(id=1)
            self.stdout.write(self.style.SUCCESS(f'✓ 默认用户已存在: {admin_user.username}'))
            return admin_user
        except User.DoesNotExist:
            if skip_admin:
                self.stdout.write(self.style.WARNING('默认用户（ID=1）不存在，且设置了 --skip-admin'))
                return None

            # 创建 admin 用户
            admin_user = User.objects.create_superuser(
                username='admin',
                email='admin@example.com',
                password='123456'
            )
            # 确保 ID 为 1
            if admin_user.id != 1:
                self.stdout.write(self.style.WARNING(f'创建的用户 ID={admin_user.id}，不是预期的 ID=1'))

            self.stdout.write(self.style.SUCCESS(f'✓ 创建默认用户: {admin_user.username} (ID={admin_user.id})'))
            return admin_user

    def _create_official_account_template(self, admin_user, force):
        """创建官方账户模板（仅使用 JSON 单一数据源）"""
        template_name = "中国用户标准账户模板"

        # 检查模板是否已存在
        existing_template = AccountTemplate.objects.filter(
            name=template_name,
            is_official=True
        ).first()

        if existing_template:
            if force:
                existing_template.delete()
                self.stdout.write(self.style.WARNING(f'删除现有官方模板: {template_name}'))
            else:
                self.stdout.write(self.style.WARNING(f'官方账户模板已存在: {template_name}，使用 --force 强制重建'))
                return

        # 创建模板（必须从 project/fixtures/official_templates/account.json 加载）
        with transaction.atomic():
            account_data = load_official_account_data()
            if not account_data:
                raise CommandError(
                    "官方账户模板 JSON 缺失或无效，请在 project/fixtures/official_templates/account.json "
                    "配置后再运行 init_official_templates。"
                )

            template = AccountTemplate.objects.create(
                name=account_data["name"],
                description=account_data.get("description", ""),
                version=account_data.get("version", "1.0.0"),
                update_notes=account_data.get("update_notes"),
                is_public=True,
                is_official=True,
                owner=admin_user,
            )
            for item in account_data["items"]:
                AccountTemplateItem.objects.create(
                    template=template,
                    account_path=item["account_path"],
                    enable=item.get("enable", True),
                    reconciliation_rule=item.get("reconciliation_rule") or "",
                    description=(item.get("description") or "").strip() or "",
                )

            self.stdout.write(self.style.SUCCESS(
                f'✓ 创建官方账户模板: {account_data["name"]} ({len(account_data["items"])} 个账户)'
            ))

    def _apply_account_templates_to_admin(self, admin_user):
        """应用账户模板到 admin 用户"""
        from project.apps.account.signals import apply_official_account_templates

        # 检查是否已有账户
        existing_count = Account.objects.filter(owner=admin_user).count()
        if existing_count > 0:
            self.stdout.write(self.style.WARNING(
                f'admin 用户已有 {existing_count} 个账户，跳过自动应用'
            ))
            return

        apply_official_account_templates(admin_user)
        final_count = Account.objects.filter(owner=admin_user).count()
        self.stdout.write(self.style.SUCCESS(
            f'✓ 为 admin 用户创建了 {final_count} 个账户'
        ))

    def _create_official_tag_template(self, admin_user, force):
        """创建官方标签模板（仅使用 JSON 单一数据源）"""
        template_name = "官方标签模板"

        existing_template = TagTemplate.objects.filter(
            name=template_name,
            is_official=True,
        ).first()

        if existing_template:
            if force:
                existing_template.delete()
                self.stdout.write(self.style.WARNING(f'删除现有官方标签模板: {template_name}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'官方标签模板已存在: {template_name}，使用 --force 强制重建'
                ))
                return

        with transaction.atomic():
            tag_data = load_official_tag_data()
            if not tag_data:
                raise CommandError(
                    "官方标签模板 JSON 缺失或无效，请在 project/fixtures/official_templates/tag.json "
                    "配置后再运行 init_official_templates。"
                )

            template = TagTemplate.objects.create(
                name=tag_data["name"],
                description=tag_data.get("description", ""),
                version=tag_data.get("version", "1.0.0"),
                update_notes=tag_data.get("update_notes"),
                is_public=True,
                is_official=True,
                owner=admin_user,
            )
            for item in tag_data["items"]:
                TagTemplateItem.objects.create(
                    template=template,
                    tag_path=item["tag_path"],
                    enable=item.get("enable", True),
                    description=(item.get("description") or "").strip(),
                )

            self.stdout.write(self.style.SUCCESS(
                f'✓ 创建官方标签模板: {tag_data["name"]} ({len(tag_data["items"])} 个标签)'
            ))

    def _apply_tag_templates_to_admin(self, admin_user):
        """应用标签模板到 admin 用户"""
        from project.apps.tags.signals import apply_official_tag_templates

        existing_count = Tag.objects.filter(owner=admin_user).count()
        if existing_count > 0:
            self.stdout.write(self.style.WARNING(
                f'admin 用户已有 {existing_count} 个标签，跳过自动应用'
            ))
            return

        apply_official_tag_templates(admin_user)
        final_count = Tag.objects.filter(owner=admin_user).count()
        self.stdout.write(self.style.SUCCESS(
            f'✓ 为 admin 用户创建了 {final_count} 个标签'
        ))

    def _ensure_format_config(self, admin_user):
        """确保 admin 用户有格式化配置"""
        config, created = FormatConfig.objects.get_or_create(
            owner=admin_user,
            defaults={
                'flag': '*',
                'show_note': True,
                'show_tag': True,
                'show_time': True,
                'show_uuid': True,
                'show_status': True,
                'show_discount': True,
                'income_template': 'Income:Transfer:Organizational',
                'commission_template': 'Expenses:Finance:Commission',
                'reconciliation_fallback_account': 'Equity:Adjustments',
                'currency': 'CNY',
                'ai_model': 'BERT'
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS('✓ 创建 admin 用户的格式化配置'))
        else:
            self.stdout.write(self.style.SUCCESS('✓ admin 用户的格式化配置已存在'))

    def _create_official_mapping_templates(self, admin_user, force):
        """创建官方映射模板（仅使用 project/fixtures/official_templates/*.json 单一数据源）"""

        # 检查现有官方映射模板
        expense_template = Template.objects.filter(name='官方支出映射', is_official=True).first()
        income_template = Template.objects.filter(name='官方收入映射', is_official=True).first()
        assets_template = Template.objects.filter(name='官方资产映射', is_official=True).first()

        # 支出映射模板（仅从 mapping_expense.json 加载）
        if not expense_template or force:
            if expense_template and force:
                expense_template.delete()

            expense_data = load_official_mapping_data("expense")
            if not expense_data:
                raise CommandError(
                    "官方支出映射 JSON 缺失或无效，请在 project/fixtures/official_templates/mapping_expense.json "
                    "配置后再运行 init_official_templates。"
                )
            expense_template = Template.objects.create(
                name=expense_data["name"],
                description=expense_data.get("description", ""),
                type='expense',
                is_public=True,
                is_official=True,
                version=expense_data.get("version", "1.0.0"),
                update_notes=expense_data.get("update_notes"),
                owner=admin_user,
            )
            for item in expense_data["items"]:
                TemplateItem.objects.create(
                    template=expense_template,
                    key=item["key"],
                    payee=(item.get("payee") or "").strip() or None,
                    account=(item.get("account") or "").strip() or None,
                    currency=(item.get("currency") or "").strip() or None,
                    tag_paths=normalize_mapping_tag_paths(item),
                )
            self.stdout.write(self.style.SUCCESS(
                f'✓ 创建官方支出映射模板 ({len(expense_data["items"])} 项)'
            ))

        # 资产映射模板（仅从 mapping_assets.json 加载）
        if not assets_template or force:
            if assets_template and force:
                assets_template.delete()

            assets_data = load_official_mapping_data("assets")
            if not assets_data:
                raise CommandError(
                    "官方资产映射 JSON 缺失或无效，请在 project/fixtures/official_templates/mapping_assets.json "
                    "配置后再运行 init_official_templates。"
                )
            assets_template = Template.objects.create(
                name=assets_data["name"],
                description=assets_data.get("description", ""),
                type='assets',
                is_public=True,
                is_official=True,
                version=assets_data.get("version", "1.0.0"),
                update_notes=assets_data.get("update_notes"),
                owner=admin_user,
            )
            for item in assets_data["items"]:
                TemplateItem.objects.create(
                    template=assets_template,
                    key=item["key"],
                    full=(item.get("full") or "").strip() or None,
                    account=(item.get("account") or "").strip() or None,
                )
            self.stdout.write(self.style.SUCCESS(
                f'✓ 创建官方资产映射模板 ({len(assets_data["items"])} 项)'
            ))

        # 收入映射模板（仅从 mapping_income.json 加载）
        if not income_template or force:
            if income_template and force:
                income_template.delete()

            income_data = load_official_mapping_data("income")
            if not income_data:
                raise CommandError(
                    "官方收入映射 JSON 缺失或无效，请在 project/fixtures/official_templates/mapping_income.json "
                    "配置后再运行 init_official_templates。"
                )
            income_template = Template.objects.create(
                name=income_data["name"],
                description=income_data.get("description", ""),
                type='income',
                is_public=True,
                is_official=True,
                version=income_data.get("version", "1.0.0"),
                update_notes=income_data.get("update_notes"),
                owner=admin_user,
            )
            for item in income_data["items"]:
                TemplateItem.objects.create(
                    template=income_template,
                    key=item["key"],
                    payer=item.get("payer") if item.get("payer") is not None else None,
                    account=(item.get("account") or "").strip() or None,
                )
            self.stdout.write(self.style.SUCCESS(
                f'✓ 创建官方收入映射模板 ({len(income_data["items"])} 项)'
            ))

    def _sync_official_expense_template_tag_paths(self):
        """将 mapping_expense.json 中的 tags 同步到官方支出 TemplateItem.tag_paths。"""
        expense_template = Template.objects.filter(name='官方支出映射', is_official=True).first()
        if not expense_template:
            return

        expense_data = load_official_mapping_data('expense')
        if not expense_data:
            self.stdout.write(self.style.WARNING('无法加载 mapping_expense.json，跳过标签路径同步'))
            return

        tag_by_key = {
            item['key']: normalize_mapping_tag_paths(item)
            for item in expense_data['items']
        }
        updated = 0
        for item in TemplateItem.objects.filter(template=expense_template):
            new_paths = tag_by_key.get(item.key, [])
            if item.tag_paths != new_paths:
                item.tag_paths = new_paths
                item.save(update_fields=['tag_paths', 'modified'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'✓ 同步官方支出映射标签路径 ({updated} 项已更新)'
        ))

    def _apply_mapping_templates_to_admin(self, admin_user):
        """应用映射模板到 admin 用户"""
        from project.apps.maps.signals import apply_official_templates
        from project.apps.maps.models import Expense, Assets, Income

        # 检查是否已有映射
        existing_expenses = Expense.objects.filter(owner=admin_user).count()
        existing_assets = Assets.objects.filter(owner=admin_user).count()
        existing_incomes = Income.objects.filter(owner=admin_user).count()
        total_existing = existing_expenses + existing_assets + existing_incomes

        if total_existing > 0:
            self.stdout.write(self.style.WARNING(
                f'admin 用户已有 {total_existing} 个映射，跳过自动应用'
            ))
            return

        apply_official_templates(admin_user)

        final_expenses = Expense.objects.filter(owner=admin_user).count()
        final_assets = Assets.objects.filter(owner=admin_user).count()
        final_incomes = Income.objects.filter(owner=admin_user).count()

        self.stdout.write(self.style.SUCCESS(
            f'✓ 为 admin 用户创建映射: 支出={final_expenses}, 资产={final_assets}, 收入={final_incomes}'
        ))

    def _create_sample_files_for_admin(self, admin_user):
        """为 admin 用户创建案例文件。始终按 fixtures 强制覆盖（删除旧文件并重建），
        删除语义与界面一致：无其他引用时一并删除 OSS 对象及 bean 相关文件。
        """
        import logging
        import os
        from project.apps.file_manager.models import Directory, File
        from project.apps.translate.models import ParseFile
        from project.utils.storage_factory import get_storage_client
        from project.utils.file import BeanFileManager

        logger = logging.getLogger(__name__)

        # 获取或创建 Root 目录
        root_dir = Directory.objects.filter(
            name='Root',
            owner=admin_user,
            parent__isnull=True
        ).first()

        if not root_dir:
            root_dir = Directory.objects.create(
                name='Root',
                owner=admin_user,
                parent=None
            )

        # 案例文件始终强制初始化：若已存在则先按「删除文件」语义清理（bean、OSS、记录）
        existing_files = File.objects.filter(
            owner=admin_user,
            directory=root_dir,
            name__in=['完整测试_微信.csv', '完整测试_支付宝.csv']
        )
        storage_client = get_storage_client()

        if existing_files.exists():
            for file_obj in list(existing_files):
                bean_relative_path = file_obj.get_bean_relative_path()
                BeanFileManager.remove_bean_from_trans_main(admin_user, bean_relative_path)
                BeanFileManager.delete_bean_file(admin_user, bean_relative_path)
                other_refs = File.objects.filter(
                    storage_name=file_obj.storage_name
                ).exclude(id=file_obj.id).exists()
                if not other_refs:
                    try:
                        storage_client.delete_file(file_obj.storage_name)
                    except Exception as e:
                        logger.warning("删除案例文件 OSS 对象失败 %s: %s", file_obj.storage_name, e)
                file_obj.delete()
            self.stdout.write(self.style.WARNING('删除现有案例文件'))

        # 案例文件配置
        # 使用与 official_templates 相同的路径构建方式
        from django.conf import settings
        from pathlib import Path
        PROJECT_DIR = Path(settings.BASE_DIR) / "project"
        SAMPLE_FILES_DIR = PROJECT_DIR / "fixtures" / "sample_files"
        
        sample_files = [
            {
                'name': '完整测试_微信.csv',
                'directory': root_dir,
                'local_path': str(SAMPLE_FILES_DIR / '完整测试_微信.csv'),
                'content_type': 'text/csv'
            },
            {
                'name': '完整测试_支付宝.csv',
                'directory': root_dir,
                'local_path': str(SAMPLE_FILES_DIR / '完整测试_支付宝.csv'),
                'content_type': 'text/csv'
            }
        ]

        storage_client = get_storage_client()
        created_files = []

        for file_config in sample_files:
            local_path = file_config['local_path']

            # 检查本地文件是否存在
            if not os.path.exists(local_path):
                self.stdout.write(self.style.WARNING(f'本地文件不存在: {local_path}，跳过'))
                continue

            # 读取文件内容
            with open(local_path, 'rb') as f:
                file_content = f.read()

            # 生成文件哈希和存储名称
            import hashlib
            hasher = hashlib.sha256()
            hasher.update(file_content)
            file_hash = hasher.hexdigest()
            file_extension = os.path.splitext(file_config['name'])[1]
            storage_name = f"{file_hash}{file_extension}"

            # 上传到存储
            from io import BytesIO
            file_stream = BytesIO(file_content)

            success = storage_client.upload_file(
                storage_name,
                file_stream,
                content_type=file_config['content_type']
            )

            if not success:
                self.stdout.write(self.style.ERROR(f'文件上传失败: {file_config["name"]}'))
                continue

            # 创建文件记录
            file_obj = File.objects.create(
                name=file_config['name'],
                directory=file_config['directory'],
                storage_name=storage_name,
                size=len(file_content),
                owner=admin_user,
                content_type=file_config['content_type']
            )

            # 创建解析记录
            parse_file = ParseFile.objects.create(file=file_obj)

            # 创建对应的 .bean 文件
            bean_relative_path = BeanFileManager.create_bean_file(
                admin_user.username,
                file_config['name'],
                file_obj.get_bean_dir()
            )
            # 上传文件时即向trans/main.bean增加对应文件的include
            BeanFileManager.add_bean_to_trans_main(
                admin_user.username,
                bean_relative_path
            )

            created_files.append(file_config['name'])

        self.stdout.write(self.style.SUCCESS(
            f'✓ 为 admin 用户创建案例文件: {len(created_files)} 个文件'
        ))
        for filename in created_files:
            self.stdout.write(f'  - {filename}')

