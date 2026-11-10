# project/apps/translate/services/steps.py
from typing import Dict
from project.utils.file import BeanFileManager, convert_to_csv_bytes,convert_to_utf8, create_text_stream
from project.utils.exceptions import UnsupportedFileTypeError, DecryptionError
from project.apps.translate.services.pipeline import Step
from project.apps.translate.services.init.bill_init_factory import InitFactory
from project.apps.translate.services.parse.filters import TransactionFilter
from project.apps.translate.services.parse.transaction_parser import single_parse_transaction
from project.apps.translate.services.parse.link_resolver import assign_transaction_links
from project.apps.translate.services.asset_provisioning import provision_missing_asset_accounts
from project.apps.translate.services.parse_api_key import resolve_parse_llm
from project.apps.translate.services.similarity import build_similarity_model
from project.apps.translate.services.parse.installment_expander import (
    collect_refund_parent_uuids,
    expand_parsed_entry,
)
from project.apps.translate.services.alipay_refund_peer import (
    build_ledger_index_for_user,
    build_raw_payment_index,
    resolve_alipay_refund_peer,
)
from project.apps.translate.views.AliPay import alipay_is_refund_row, alipay_parent_uuid
from project.apps.translate.utils import *
from project.apps.translate.views.AliPay import *
from project.apps.translate.views.WeChat import *
from project.apps.translate.views.BOC_Debit import *
from project.apps.translate.views.CMB_Credit import *
from project.apps.translate.views.ICBC_Debit import *
from project.apps.translate.views.CCB_Debit import *
import logging
import os


logger = logging.getLogger(__name__)


def allocate_unique_cache_key(base: str, seen: Dict[str, int]) -> str:
    """同一批账单内 cache_key 必须唯一。

    交易订单号可能重复（组合支付、多笔关联），审核页与 Redis 都以 cache_key 定位条目。
    首次保持原值，后续追加 ``--n``（URL 安全，避免与支付宝退款 ``parent_suffix`` 冲突）。
    """
    count = seen.get(base, 0) + 1
    seen[base] = count
    if count == 1:
        return base
    return f"{base}--{count}"


class ConvertToCSVStep(Step):
    """对象转换步骤：将上传文件转换为CSV格式的文件对象"""
    def execute(self, context: Dict) -> Dict:
        uploaded_file = context['uploaded_file']
        password = context['args'].get("password")

        try:
            # 转换为CSV字节内容
            csv_bytes = convert_to_csv_bytes(uploaded_file, password)
            if csv_bytes is None:
                return self._error(context, "文件转换结果为空，当前账单类型可能不支持")

            # 检测编码并转换为UTF-8
            utf8_csv_bytes = convert_to_utf8(csv_bytes)

            # 创建内存文件对象
            # csv_file_object = create_in_memory_file(uploaded_file.name, utf8_csv_bytes)
            csv_file_object = create_text_stream(uploaded_file.name, utf8_csv_bytes)

            # 存储到上下文
            context['csv_file_object'] = csv_file_object
            return context

        except DecryptionError as e:
            code = 'DECRYPTION_REQUIRED' if '请提供解密密码' in str(e) else 'DECRYPTION_FAILED'
            return self._error(context, f"文件转换失败: {str(e)}", code=code)
        except UnsupportedFileTypeError as e:
            return self._error(context, f"文件转换失败: {str(e)}", code='UNSUPPORTED_BILL')
        except Exception as e:
            return self._error(context, f"CSV转换步骤异常: {str(e)}")


class InitializeBillStep(Step):
    """账单初始化步骤（集成策略模式+工厂方法）"""
    def execute(self, context: Dict) -> Dict:
        # 获取文本流对象
        csv_file = context['csv_file_object']

        # 重置文件指针以确保从头开始读取
        csv_file.seek(0)

        # 读取第一行
        first_line = csv_file.readline().strip()
        year = first_line[:4]
        card_number = card_number_get_key(first_line)

        try:
            # 工厂方法创建策略
            strategy = InitFactory.create_strategy(first_line)

            # 识别账单类型后回到文件开头，避免 readline 导致首行数据偏移
            csv_file.seek(0)

            # 策略执行初始化
            initialized_bill = strategy.init(csv_file, card_number=card_number, year=year)

            # 结果存入上下文
            context['initialized_bill'] = initialized_bill
            context['bill_type'] = initialized_bill[0]['bill_identifier']
            return context

        except ValueError as e:  # 无匹配策略
            return self._error(context, f"不支持的账单类型: {str(e)}", code='UNSUPPORTED_BILL')
        except Exception as e:  # 初始化异常
            return self._error(context, f"账单初始化异常: {str(e)}")


class PreFilterStep(Step):
    """预过滤步骤：基于原始数据的简单规则过滤"""
    def execute(self, context):
        bill_data = context['initialized_bill']
        args = context['args']
        bill_type = context['bill_type']
        # print(context['bill_type'])  # alipay
        try:
            filter = TransactionFilter(args, bill_type)
            # print(bill_data)  # [{'transaction_time': '2024-02-25 20:01:48', 'transaction_category': '母婴亲子', 'counterparty': '十月**店', 'commodity': '【天猫U先】十月结晶会员尊享精致妈咪出行必备生活随心包4件套 等多件', 'transaction_type': '/', 'amount': 14.8, 'payment_method': '亲情卡(凯义(王凯义))', 'transaction_status': '交易成功', 'notes': '/', 'bill_identifier': 'alipay', 'uuid': '2024022522001174561439593142', 'discount': False}]
            filter_data = filter.apply_pre_filters(bill_data)
            context['prefilter_bill'] = filter_data
            logger.info(f"预过滤后剩余记录数: {len(filter_data)}/{len(bill_data)}")
            return context
        except Exception as e:
            return self._error(context, f"预过滤步骤异常: {str(e)}")


class ParseStep(Step):
    """交易解析步骤：解析账单中的交易数据"""
    def execute(self,  context: Dict) -> Dict:
        import hashlib
        owner_id = context['owner_id']
        config = context['config']
        bill_data = context['prefilter_bill']

        try:
            user = context.get('user')
            if not user:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.filter(id=owner_id).first()

            ledger_index = build_ledger_index_for_user(user) if user else {}
            raw_payment_index = build_raw_payment_index(bill_data)
            parse_cache: Dict[str, Dict] = {}
            seen_cache_keys: Dict[str, int] = {}
            refund_parent_uuids = collect_refund_parent_uuids(bill_data)

            # 每个文件只构建一次，BERT 兜底状态在整批条目间共享
            llm = resolve_parse_llm(config)
            similarity_model = build_similarity_model(
                getattr(config, 'ai_model', None),
                llm,
            )
            logger.info(
                "解析引擎: %s, LLM 来源: %s",
                getattr(config, 'ai_model', None) or 'BERT',
                llm.source if llm else 'none',
            )

            def _lazy_parse_payment(payment_row: Dict) -> Dict:
                entry = single_parse_transaction(
                    payment_row, owner_id, config, None, similarity_model=similarity_model
                )
                entry['_original_row'] = payment_row
                expanded = expand_parsed_entry(entry, refund_parent_uuids)
                for item in expanded:
                    if item.get('installment_role') in (None, 'purchase'):
                        return item
                return expanded[0]

            context.setdefault('parsed_data', [])
            args = context.get('args') or {}
            no_ignore = args.get('no_ignore') is True

            for row in bill_data:
                refund_peer = None
                if alipay_is_refund_row(row):
                    refund_peer = resolve_alipay_refund_peer(
                        alipay_parent_uuid(row),
                        parse_cache,
                        raw_payment_index,
                        ledger_index,
                        _lazy_parse_payment,
                    )
                    # 原单已关闭被预过滤、或不在本批/账本：退款入账会凭空增加资产
                    # 「关闭默认忽略」时全部保留，便于对照原始账单
                    if refund_peer is None and not no_ignore:
                        logger.info(
                            "忽略无原单关联的支付宝退款 uuid=%s parent=%s",
                            row.get("uuid"),
                            alipay_parent_uuid(row),
                        )
                        continue
                    if refund_peer is None and no_ignore:
                        logger.info(
                            "关闭默认忽略：保留无原单关联的支付宝退款 uuid=%s parent=%s",
                            row.get("uuid"),
                            alipay_parent_uuid(row),
                        )

                # 每条账单行独立解析。parse_cache 仅供退款关联原单，不可复用为当前行结果
                # （组合支付会共享交易订单号，复用会导致金额/账户被覆盖并在审核页撞 uuid）。
                parsed_entry = single_parse_transaction(
                    row,
                    owner_id,
                    config,
                    None,
                    refund_peer=refund_peer,
                    similarity_model=similarity_model,
                )
                parsed_entry['_original_row'] = row
                expanded_entries = expand_parsed_entry(parsed_entry, refund_parent_uuids)

                payment_uuid = (row.get('uuid') or '').strip()
                for entry in expanded_entries:
                    if entry.get('uuid'):
                        base_key = str(entry['uuid'])
                    else:
                        base_key = hashlib.md5(str(row).encode()).hexdigest()
                    entry['cache_key'] = allocate_unique_cache_key(base_key, seen_cache_keys)

                    if (
                        payment_uuid
                        and not alipay_is_refund_row(row)
                        and payment_uuid not in parse_cache
                        and entry.get('installment_role') in (None, 'purchase')
                    ):
                        parse_cache[payment_uuid] = entry

                    context['parsed_data'].append(entry)

            # 二次扫描：为同订单/退款关联条目挂 Beancount ^原单号（需在 CacheStep pop _original_row 之前）
            if context.get('parsed_data'):
                assign_transaction_links(context['parsed_data'])

            # 解析成功后补齐资产账户与映射；失败不影响本次解析结果
            auto_provision = bool(
                getattr(config, 'auto_provision_assets', True)
                and (context.get('args') or {}).get('auto_provision_assets', True)
            )
            try:
                provision_missing_asset_accounts(
                    context.get('parsed_data') or [],
                    context.get('bill_type') or '',
                    owner_id,
                    enabled=auto_provision,
                )
            except Exception:
                logger.warning('自动补齐资产账户失败', exc_info=True)
        except Exception as e:
            import traceback
            logger.error(f"解析步骤详细错误: {traceback.format_exc()}")
            return self._error(context, f"解析步骤异常: {str(e)}")
        return context


class PostFilterStep(Step):
    """后过滤步骤：基于解析后的结构化数据进行过滤"""
    def execute(self, context: Dict) -> Dict:
        parsed_data = context['parsed_data']
        args = context['args']
        bill_type = context['bill_type']
        try:
            filter = TransactionFilter(args, bill_type)
            # print(parsed_data)  # [{'date': '2024-02-25', 'time': '20:01:48', 'uuid': '2024022522001174561439593142', 'status': 'ALiPay - 交易成功', 'payee': '十月结晶', 'note': '【天猫U先】十月结晶会员尊享精致妈咪出行必备生活随心包4件套 等多件', 'tag': None, 'balance': None, 'balance_date': '2024-02-26', 'expense': 'Expenses:Shopping:Parent', 'expenditure_sign': '', 'account': 'Equity:OpenBalance', 'account_sign': '-', 'amount': '14.80', 'discount': False, 'currency': 'CNY', 'selected_expense_key': '十月结晶', 'expense_candidates_with_score': [{'key': '等多件', 'score': 0.5471}, {'key': '出行', 'score': 0.557}, {'key': '**', 'score': 0.5499}, {'key': '十月结晶', 'score': 0.5642}], 'actual_amount': '14.80', '_original_row': {'transaction_time': '2024-02-25 20:01:48', 'transaction_category': '母婴亲子', 'counterparty': '十月**店', 'commodity': '【天猫U先】十月结晶会员尊享精致妈咪出行必备生活随心包4件套 等多件', 'transaction_type': '/', 'amount': 14.8, 'payment_method': '亲情卡(凯义(王凯义))', 'transaction_status': '交易成功', 'notes': '/', 'bill_identifier': 'alipay', 'uuid': '2024022522001174561439593142', 'discount': False}, 'cache_key': '2024022522001174561439593142'}]
            filtered_data = filter.apply_post_filters(parsed_data)
            context['filtered_data'] = filtered_data
            logger.info(f"后过滤后剩余记录数: {len(filtered_data)}/{len(parsed_data)}")
            return context
        except Exception as e:
            return self._error(context, f"后过滤步骤异常: {str(e)}")


class CacheStep(Step):
    """结果缓存步骤：将处理结果缓存到数据库或其他存储中供重新解析步骤使用"""
    def execute(self,  context: Dict) -> Dict:
        from django.core.cache import cache


        parsed_data = context['parsed_data']
        args = context['args']
        for entry in parsed_data:
            cache_key = entry['cache_key']
            # 转为 original_row 供 FormatStep 使用；缓存仍存独立副本
            original_row = entry.pop('_original_row', None) or entry.get('original_row') or {}
            entry['original_row'] = original_row
            entry['counterparty'] = original_row.get('counterparty') or ''
            entry['commodity'] = original_row.get('commodity') or ''
            # 缓存中的 parsed_entry 不含 original_row，避免与顶层重复
            parsed_for_cache = {k: v for k, v in entry.items() if k != 'original_row'}
            cache_data = {
                "parsed_entry": parsed_for_cache,
                "original_row": original_row,
                # 重解析沿用首页本次选择的引擎
                "parse_engine": getattr(context['config'], 'ai_model', None) or 'BERT',
            }
            # 如果写入标志为False,则写入缓存
            if not args.get('write', True):
                cache.set(cache_key, cache_data, timeout=3600)
        return context


class FormatStep(Step):
    """交易格式化步骤：将交易数据格式化为.bean文本格式"""
    def execute(self,  context: Dict) -> Dict:
        parsed_data = context['filtered_data']
        config = context['config']
        if 'formatted_data' not in context:
            context['formatted_data'] = []
        for entry in parsed_data:
            formatted = FormatData.format_instance(entry, config=config)
            formatted_text = formatted.rstrip() if formatted else ''
            original_row = entry.get('original_row') or entry.get('_original_row') or {}
            formatted_dict = {
                "formatted": formatted_text,
                "edited_formatted": formatted_text,
                "selected_expense_key": entry.get("selected_expense_key"),
                "expense_candidates_with_score": entry.get("expense_candidates_with_score", []),
                "counterparty": entry.get("counterparty", ""),
                "commodity": entry.get("commodity", ""),
                "payment_method": original_row.get("payment_method", ""),
                "transaction_type": original_row.get("transaction_type", ""),
                "uuid": entry.get("uuid") or entry.get("cache_key"),
                "id": entry.get("cache_key"),
                "installment_role": entry.get("installment_role"),
                "installment_period": entry.get("installment_period"),
                "tag_details": entry.get("tag_details") or [],
                "original_row": original_row,
            }
            context['formatted_data'].append(formatted_dict)
        return context


class FileWritingStep(Step):
    """文件写入步骤：将处理后的数据写入文件（可选）"""
    def execute(self,  context: Dict) -> Dict:
        if context['args']['write']:
            username = context['username']
            
            # 尝试从 context 获取 user 对象，如果没有则从 username 获取
            user = context.get('user')
            if not user:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                try:
                    user = User.objects.get(username=username)
                except User.DoesNotExist:
                    # 如果找不到用户，回退到使用 username
                    user = username
            
            formatted_data = "\n\n".join([entry['formatted'].rstrip() for entry in context['formatted_data']])

            original_filename = context['uploaded_file'].name
            relative_dir = (context.get('args') or {}).get('bean_relative_dir', '') or ''
            bean_file_path = BeanFileManager.get_bean_file_path(user, original_filename, relative_dir)

            # 确保trans目录存在
            # BeanFileManager.ensure_trans_directory(user)

            # 写入文件到trans目录
            with open(bean_file_path, 'w', encoding='utf-8') as f:
                f.write(formatted_data)

            # 注意：include语句的添加已在上传文件时完成，解析功能仅处理文件内容的写入

            # 写入后，对账生成的 pad 可能因真实交易落入断言容差而变为无用，
            # 自动注释之（best-effort，失败不影响写入结果）
            from project.apps.reconciliation.services.reconciliation_comment_service import (
                ReconciliationCommentService,
            )
            ReconciliationCommentService.cleanup_unused_pads(user)

        return context