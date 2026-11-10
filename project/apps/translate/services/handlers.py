# project/apps/translate/services/handlers.py
import datetime
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from project.apps.maps.models import Expense, Income
from project.apps.translate.utils import *
from project.apps.translate.views.AliPay import *
from project.apps.translate.services.ledger_uuid_index import RefundPeerSnapshot
from project.apps.translate.views.WeChat import *
from project.apps.translate.views.BOC_Debit import *
from project.apps.translate.views.CMB_Credit import *
from project.apps.translate.views.ICBC_Debit import *
from project.apps.translate.views.CCB_Debit import *
from project.apps.translate.services.mapping_provider import get_mapping_provider
import logging

logger = logging.getLogger(__name__)


class AccountHandler:
    def __init__(self, data):
        self.key = None
        self.key_list = None
        self.type = None
        self.account = ASSETS_OTHER
        self.bill = data['bill_identifier']
        self.balance = data['transaction_type']
        self.selected_asset_instance = None  # 新增：存储选中的资产映射实例
        self.asset_tags = []  # 新增：存储资产映射关联的标签
        self.asset_tag_sources = []  # tag + source 元数据
        self._asset_mappings = []  # 缓存资产映射数据

    def find_asset_by_key(self, key: str):
        """根据 key 查找资产映射"""
        return next((m for m in self._asset_mappings if m.key == key), None)

    def initialize_type(self, data):
        if self.bill == BILL_ALI:
            self.type = alipay_get_type(data)
        elif self.bill == BILL_WECHAT:
            self.type = wechatpay_get_type(data)

    def initialize_key_list(self, ownerid):
        # 使用映射数据提供者获取资产映射
        provider = get_mapping_provider(ownerid)
        asset_mappings = provider.get_asset_mappings(enable_only=True)
        self.key_list = [m.key for m in asset_mappings]
        self._asset_mappings = asset_mappings  # 缓存以供后续使用

    def initialize_key(self, data):
        if self.bill == BILL_ALI:
            self.key = alipay_initalize_key(data)
        elif self.bill == BILL_WECHAT:
            self.key = wechatpay_initalize_key(self, data)
        elif self.bill == BILL_CMB_CREDIT:
            self.key = cmb_credit_init_key(data)
        elif self.bill == BILL_BOC_DEBIT:
            self.key = boc_debit_init_key(data)
        elif self.bill == BILL_ICBC_DEBIT:
            self.key = icbc_debit_init_key(data)
        elif self.bill == BILL_CCB_DEBIT:
            self.key = ccb_debit_init_key(data)

    def _load_asset_tags(self, asset_key: str, ownerid: int) -> None:
        """加载资产映射关联的标签"""
        try:
            # 从缓存的映射数据中查找
            asset_instance = next((m for m in self._asset_mappings if m.key == asset_key), None)
            if asset_instance:
                self.selected_asset_instance = asset_instance
                self.asset_tags = list(asset_instance.tags.filter(enable=True))
                self.asset_tag_sources = [
                    {
                        'tag': tag,
                        'source': {
                            'type': 'mapping',
                            'key': asset_key,
                            'mapping_type': 'asset',
                        },
                    }
                    for tag in self.asset_tags
                ]
        except Exception as e:
            logger.error(f"加载资产标签失败: {str(e)}")
            self.asset_tags = []
            self.asset_tag_sources = []

    def get_asset_tags(self):
        """获取资产映射的标签列表"""
        return self.asset_tags

    def get_asset_tag_sources(self):
        """获取资产映射标签及来源"""
        return self.asset_tag_sources

    def get_account(self, data, ownerid, fallback_account=None):
        from project.apps.translate.utils import DEFAULT_FALLBACK_ACCOUNT
        if fallback_account is None:
            fallback_account = DEFAULT_FALLBACK_ACCOUNT
        self.initialize_key(data)
        self.initialize_key_list(ownerid)  # 根据收支情况获取数据库中key的所有值，将其处理为列表
        self.initialize_type(data)
        self.status = data['transaction_status']
        actual_assets = get_default_assets(ownerid=ownerid)
        account = self.account

        if self.bill == BILL_ALI and alipay_uses_fallback_payment_method(data.get('payment_method')):
            return fallback_account

        # 银行账单同样使用 transaction_type「收入/支出」，必须先按 bill 分流，否则会落入支付宝/微信分支且无法匹配，保持默认 Assets:Other
        if self.bill == BILL_BOC_DEBIT:
            account = boc_debit_get_account(self, ownerid)
        elif self.bill == BILL_CMB_CREDIT:
            account = cmb_credit_get_account(self, ownerid)
        elif self.bill == BILL_ICBC_DEBIT:
            account = icbc_debit_get_account(self, ownerid)
        elif self.bill == BILL_CCB_DEBIT:
            account = ccb_debit_get_account(self, ownerid)
        elif self.balance == "收入":
            if self.bill == BILL_ALI:
                account = alipay_get_income_account(self, actual_assets, ownerid)
            elif self.bill == BILL_WECHAT:
                account = wechatpay_get_income_account(self, actual_assets, ownerid)
        elif self.balance == "支出":
            if self.bill == BILL_ALI:
                account = alipay_get_expense_account(self, actual_assets, ownerid)
            elif self.bill == BILL_WECHAT:
                account = wechatpay_get_expense_account(self, actual_assets, ownerid)
        elif self.balance == "/" or self.balance == "不计收支":  # 收/支栏 值为/
            if self.bill == BILL_ALI:
                account = alipay_get_balance_account(self, data, actual_assets, ownerid, fallback_account)
            elif self.bill == BILL_WECHAT:
                account = wechatpay_get_balance_account(self, data, actual_assets, ownerid)

        # 加载资产标签
        if self.key and self.key in self.key_list:
            self._load_asset_tags(self.key, ownerid)

        return account


class ExpenseHandler:
    def __init__(
        self,
        data: Dict,
        model: str,
        similarity_model=None,
        selected_key: Optional[str] = None,
        refund_peer: Optional[RefundPeerSnapshot] = None,
    ):
        self.refund_peer = refund_peer
        self.selected_expense_instance = None
        self.selected_income_instance = None
        self.selected_key = selected_key
        self.key_list = None
        self.type = None
        self.expend = EXPENSES_OTHER
        self.income = INCOME_OTHER
        self.bill = data['bill_identifier']
        self.balance = data['transaction_type']
        self.time = datetime.strptime(data['transaction_time'], "%Y-%m-%d %H:%M:%S").time()
        self.currency = "CNY"
        self.model = model
        self.selected_expense_key = None
        self.expense_candidates_with_score = []  # 如果你想带分数
        self.mapping_tags = []  # 新增：存储映射关联的标签
        self.all_candidates_tags = []  # 新增：存储所有候选映射的标签
        self.candidate_tag_sources = []  # 候选映射标签及来源
        self._expense_mappings = []  # 缓存支出映射数据
        self._income_mappings = []  # 缓存收入映射数据
        self._asset_mappings = []  # 缓存资产映射数据

        # 相似度模型由解析入口按文件构建，避免每行重复初始化
        self.similarity_model = similarity_model

    def find_asset_by_key(self, key: str):
        """根据 key 查找资产映射"""
        return next((m for m in self._asset_mappings if m.key == key), None)

    def initialize_type(self, data: Dict) -> None:
        """初始化交易类型"""
        if self.bill == BILL_ALI:
            self.type = alipay_get_type(data)
        elif self.bill == BILL_WECHAT:
            self.type = wechatpay_get_type(data)

    def initialize_key_list(self, data: Dict, ownerid: int) -> None:
        """初始化关键字列表"""
        provider = get_mapping_provider(ownerid)

        if self.balance == "支出" or alipay_uses_fallback_payment_method(data.get('payment_method')):
            expense_mappings = provider.get_expense_mappings(enable_only=True)
            self.key_list = [m.key for m in expense_mappings]
            self._expense_mappings = expense_mappings  # 缓存
        elif self.balance == "收入":
            income_mappings = provider.get_income_mappings(enable_only=True)
            self.key_list = [m.key for m in income_mappings]
            self._income_mappings = income_mappings  # 缓存
        elif self.balance in ("/", "不计收支"):
            asset_mappings = provider.get_asset_mappings(enable_only=True)
            self.key_list = [m.key for m in asset_mappings]
            self._asset_mappings = asset_mappings  # 缓存

        # 资产映射缓存用于资产标签与对端账户查询
        if not self._asset_mappings:
            self._asset_mappings = provider.get_asset_mappings(enable_only=True)

    def _mapping_has_account(self, instance, account_attr: str = "expend") -> bool:
        """映射是否配置了账户（expend / income）。"""
        return bool(instance and getattr(instance, account_attr, None))

    def _build_candidates_with_score(
        self,
        conflict_candidates: List[Tuple[int, object]],
        scores: Optional[Dict] = None,
        account_attr: str = "expend",
    ) -> List[Dict]:
        """构建候选关键字分数列表；无映射账户的关键字语义相似度默认为 0。"""
        result = []
        for _, inst in conflict_candidates:
            if not self._mapping_has_account(inst, account_attr):
                score = 0.0
            elif scores is None:
                score = 1.0
            else:
                score = round(scores.get(inst.key, 0), 4)
            result.append({"key": inst.key, "score": score})
        return result

    def _resolve_expense_conflict(
        self,
        conflict_candidates: List[Tuple[int, object]],
        transaction_text: str,
        account_attr: str = "expend",
    ):
        account_candidates = [
            (order, inst) for order, inst in conflict_candidates
            if self._mapping_has_account(inst, account_attr)
        ]

        if not account_candidates:
            self.expense_candidates_with_score = self._build_candidates_with_score(
                conflict_candidates, account_attr=account_attr
            )
            return max(conflict_candidates, key=lambda x: x[0])[1]

        try:
            keys = [inst.key for _, inst in account_candidates]
            sim_result = self.similarity_model.calculate_similarity(transaction_text, keys)
            selected_key = sim_result["best_match"]
            scores = sim_result["scores"]
            self.selected_expense_key = selected_key
            self.expense_candidates_with_score = self._build_candidates_with_score(
                conflict_candidates, scores=scores, account_attr=account_attr
            )
            return next(inst for _, inst in account_candidates if inst.key == selected_key)
        except Exception as e:
            logger.error(f"AI处理失败：{str(e)}")
            raise e

    def _determine_food_category(self, foodtime: datetime.time) -> str:
        """根据时间确定餐饮类别"""
        if TIME_BREAKFAST_START <= foodtime <= TIME_BREAKFAST_END:
            return ":Breakfast"
        elif TIME_LUNCH_START <= foodtime <= TIME_LUNCH_END:
            return ":Lunch"
        elif TIME_DINNER_START <= foodtime <= TIME_DINNER_END:
            return ":Dinner"
        return ""

    def _expense_mapping_priority(self, expense_instance) -> int:
        """计算支出映射优先级；无映射账户时使用较低优先级。"""
        if expense_instance.expend:
            expend_priority = expense_instance.expend.account.count(":") * 100
            payee_priority = 50 if expense_instance.payee else 0
            return expend_priority + payee_priority
        return 0

    def _income_mapping_priority(self, income_instance) -> int:
        """计算收入映射优先级；无映射账户时使用较低优先级。"""
        if income_instance.income:
            return income_instance.income.account.count(":") * 100
        return 0

    def _resolve_expense_account(self, expense_instance) -> Optional[str]:
        """从支出映射实例解析账户路径；无映射账户时返回 None。"""
        if not expense_instance or not expense_instance.expend:
            return None
        expend = expense_instance.expend.account
        if expend == "Expenses:Food":
            expend += self._determine_food_category(self.time)
        return expend

    def _process_expense(self, data: Dict, ownerid: int) -> str:
        """处理支出逻辑"""

        if self.bill == BILL_BOC_DEBIT:
            matching_keys = boc_debit_filter_keys_in_blob(self.key_list, data)
        else:
            matching_keys = [k for k in self.key_list if k in data['counterparty'] or k in data['commodity']]
        conflict_candidates = []
        max_order = None

        for matching_key in matching_keys:
            # 从缓存的映射数据中查找
            expense_instance = next((m for m in self._expense_mappings if m.key == matching_key), None)
            if expense_instance:
                current_order = self._expense_mapping_priority(expense_instance)
                conflict_candidates.append((current_order, expense_instance))

                if max_order is None or current_order > max_order:
                    max_order = current_order
                    self.selected_expense_instance = expense_instance

        # 收集所有候选映射的标签
        if len(conflict_candidates) > 0:
            self._load_all_candidates_tags(conflict_candidates)

        if len(conflict_candidates) > 1 and self.model != "None":
            selected_instance = self._resolve_expense_conflict(conflict_candidates,
                f"类型：{data['transaction_category']} 商户：{data['counterparty']} 商品：{data['commodity']} 金额：{data['amount']}元")  # 构造语义化查询
            self.selected_expense_instance = selected_instance
            self.selected_expense_key = selected_instance.key
        else:
            # 只有一个候选或无候选，直接给分数
            self.expense_candidates_with_score = self._build_candidates_with_score(conflict_candidates)
            if self.selected_expense_instance:
                self.selected_expense_key = self.selected_expense_instance.key

        if self.selected_expense_instance:
            # 获取映射关联的标签
            self._load_mapping_tags(self.selected_expense_instance)

            if self.selected_key:
                # 从缓存的映射数据中查找
                self.selected_expense_instance = next((m for m in self._expense_mappings if m.key == self.selected_key), None)
                if self.selected_expense_instance:
                    self._load_mapping_tags(self.selected_expense_instance)
                    existing_keys = {item['key'] for item in self.expense_candidates_with_score}
                    if self.selected_key not in existing_keys:
                        score = 1.0 if self._mapping_has_account(self.selected_expense_instance) else 0.0
                        self.expense_candidates_with_score.append(
                            {'key': self.selected_key, 'score': score}
                        )
                    expend = self._resolve_expense_account(self.selected_expense_instance)
                    if expend:
                        self.currency = self.selected_expense_instance.currency if self.selected_expense_instance.currency else "CNY"
                        return expend, self.selected_key, self.expense_candidates_with_score
                    return self.expend, self.selected_key, self.expense_candidates_with_score
            elif self.selected_key is None:
                if self.selected_expense_instance:
                    expend = self._resolve_expense_account(self.selected_expense_instance)
                    if expend:
                        self.currency = self.selected_expense_instance.currency if self.selected_expense_instance.currency else "CNY"
                        return expend, self.selected_expense_key, self.expense_candidates_with_score

        if self.selected_key:
            manual_instance = next(
                (m for m in self._expense_mappings if m.key == self.selected_key),
                None,
            )
            if manual_instance:
                self.selected_expense_instance = manual_instance
                self._load_mapping_tags(manual_instance)
                existing_keys = {item['key'] for item in self.expense_candidates_with_score}
                if self.selected_key not in existing_keys:
                    score = 1.0 if self._mapping_has_account(manual_instance) else 0.0
                    self.expense_candidates_with_score.append(
                        {'key': self.selected_key, 'score': score}
                    )
                expend = self._resolve_expense_account(manual_instance)
                if expend:
                    self.currency = manual_instance.currency if manual_instance.currency else "CNY"
                    return expend, self.selected_key, self.expense_candidates_with_score
                return self.expend, self.selected_key, self.expense_candidates_with_score

        return self.expend, self.selected_expense_key, self.expense_candidates_with_score

    def _load_mapping_tags(self, mapping_instance):
        """加载映射关联的标签"""
        try:
            self.mapping_tags = list(mapping_instance.tags.filter(enable=True))
        except Exception as e:
            logger.error(f"加载映射标签失败: {str(e)}")
            self.mapping_tags = []

    def _load_all_candidates_tags(self, conflict_candidates: List[Tuple[int, object]]):
        """加载所有候选映射的标签"""
        try:
            all_tags = []
            candidate_tag_sources = []
            if self.balance == '收入':
                mapping_type = 'income'
            else:
                mapping_type = 'expense'
            for _, instance in conflict_candidates:
                tags = list(instance.tags.filter(enable=True))
                all_tags.extend(tags)
                for tag in tags:
                    candidate_tag_sources.append({
                        'tag': tag,
                        'source': {
                            'type': 'mapping',
                            'key': instance.key,
                            'mapping_type': mapping_type,
                        },
                    })
            # 去重，保持标签对象唯一性
            seen_tag_ids = set()
            unique_tags = []
            for tag in all_tags:
                if tag.id not in seen_tag_ids:
                    seen_tag_ids.add(tag.id)
                    unique_tags.append(tag)
            self.all_candidates_tags = unique_tags
            self.candidate_tag_sources = candidate_tag_sources
        except Exception as e:
            logger.error(f"加载候选标签失败: {str(e)}")
            self.all_candidates_tags = []
            self.candidate_tag_sources = []

    def get_mapping_tags(self):
        """获取当前映射的标签列表"""
        return self.mapping_tags

    def get_all_candidates_tags(self):
        """获取所有候选映射的标签列表"""
        return self.all_candidates_tags

    def get_candidate_tag_sources(self):
        """获取所有候选映射的标签及来源"""
        return self.candidate_tag_sources

    def _process_income(self, data: Dict, ownerid: int) -> Tuple[str, Optional[str], List[Dict]]:
        """处理收入逻辑

        Returns:
            (income_account, selected_key, candidates_with_score)
        """
        if self.bill == BILL_BOC_DEBIT:
            matching_keys = boc_debit_filter_keys_in_blob(self.key_list, data)
        else:
            matching_keys = [k for k in self.key_list if k in data['counterparty'] or k in data['commodity']]
        conflict_candidates: List[Tuple[int, object]] = []
        max_order = None

        for matching_key in matching_keys:
            # 从缓存的映射数据中查找
            income_instance = next((m for m in self._income_mappings if m.key == matching_key), None)
            if income_instance:
                income_priority = self._income_mapping_priority(income_instance)
                conflict_candidates.append((income_priority, income_instance))
                if max_order is None or income_priority > max_order:
                    max_order = income_priority
                    self.selected_income_instance = income_instance

        # 收集所有收入候选的标签
        if conflict_candidates:
            self._load_all_candidates_tags(conflict_candidates)

        # 构造候选列表与默认选中关键字
        selected_key: Optional[str] = None
        candidates_with_score: List[Dict] = []

        if len(conflict_candidates) > 1 and self.model != "None":
            # 多候选时使用相似度模型解决冲突
            selected_instance = self._resolve_expense_conflict(
                conflict_candidates,
                f"类型：{data['transaction_category']} 商户：{data['counterparty']} 商品：{data['commodity']} 金额：{data['amount']}元",
                account_attr="income",
            )
            self.selected_income_instance = selected_instance
            selected_key = selected_instance.key
            candidates_with_score = self.expense_candidates_with_score
        else:
            # 只有一个候选或无候选，直接给分数
            candidates_with_score = self._build_candidates_with_score(
                conflict_candidates, account_attr="income"
            )
            if self.selected_income_instance:
                selected_key = self.selected_income_instance.key

        # 用户手动选择关键字时覆盖（重解析）
        if self.selected_key:
            selected_income_instance = next((m for m in self._income_mappings if m.key == self.selected_key), None)
            if selected_income_instance:
                self.selected_income_instance = selected_income_instance
                selected_key = self.selected_key
                self._load_mapping_tags(selected_income_instance)
                existing_keys = {item['key'] for item in candidates_with_score}
                if self.selected_key not in existing_keys:
                    score = 1.0 if self._mapping_has_account(selected_income_instance, 'income') else 0.0
                    candidates_with_score.append({'key': self.selected_key, 'score': score})

        if self.selected_income_instance and self.selected_income_instance.income:
            return self.selected_income_instance.income.account, selected_key, candidates_with_score

        if self.selected_key and self.selected_income_instance:
            return self.income, selected_key, candidates_with_score

        return self.income, selected_key, candidates_with_score

    def get_expense(self, data: Dict, ownerid: int) -> str:
        """主处理方法"""
        self.initialize_key_list(data, ownerid)
        self.initialize_type(data)

        if self.bill == BILL_ALI and alipay_is_refund_row(data) and self.refund_peer:
            key = self.refund_peer.selected_expense_key
            candidates = [{"key": key, "score": 1.0}] if key else []
            return self.refund_peer.expense_account, key, candidates

        if self.balance == "支出" or alipay_uses_fallback_payment_method(data.get('payment_method')):
            expend, selected_expense_key, expense_candidates_with_score = self._process_expense(data, ownerid)
            return expend, selected_expense_key, expense_candidates_with_score
        elif self.balance == "收入":
            if self.bill == BILL_BOC_DEBIT and not self.selected_key:
                _peer = boc_debit_try_income_peer_asset(
                    data,
                    self._asset_mappings or [],
                    self.model,
                    self.similarity_model,
                )
                if _peer is not None:
                    self.expense_candidates_with_score = _peer.expense_candidates_with_score
                    self.mapping_tags = list(_peer.mapping_tags)
                    self.all_candidates_tags = list(self.mapping_tags)
                    return (
                        _peer.peer_account,
                        _peer.selected_key,
                        self.expense_candidates_with_score,
                    )
            income, selected_income_key, income_candidates_with_score = self._process_income(data, ownerid)
            return income, selected_income_key, income_candidates_with_score
        elif self.balance in ("/", "不计收支"):
            actual_assets = get_default_assets(ownerid=ownerid)
            if self.bill == BILL_ALI:
                expend = alipay_get_balance_expense(self, data, actual_assets, ownerid)
            elif self.bill == BILL_WECHAT:
                expend = wechatpay_get_balance_expense(self, data, actual_assets, ownerid)
            else:
                expend = self.expend
            return expend, None, []
        return self.expend, None, []

    def get_currency(self) -> str:
        return self.currency


class PayeeHandler:
    def __init__(self, data):
        self.key_list = None
        self.payee = data['counterparty']
        self.notes = data['commodity']
        self.bill = data['bill_identifier']
        self._expense_mappings = []  # 缓存映射数据

    def _finalize_payee(self, data: Dict, payee: str) -> str:
        if data['bill_identifier'] == BILL_ALI and payee == "":
            return data['counterparty']
        if data['bill_identifier'] == BILL_WECHAT and payee == "":
            payee = data['counterparty']
            if payee == "/":
                return data['transaction_time']
        return payee

    def _payee_from_mapping_key(
        self, selected_mapping_key: str, data: Dict, ownerid: int
    ) -> Optional[str]:
        """按 selected_expense_key 查支出 payee 或收入 payer；无有效字段时返回 None。"""
        provider = get_mapping_provider(ownerid)
        if data.get('transaction_type') == '收入':
            income_mappings = provider.get_income_mappings(enable_only=True)
            instance = next(
                (m for m in income_mappings if m.key == selected_mapping_key), None
            )
            if instance and instance.payer:
                return instance.payer
            return None
        expense_mappings = self._expense_mappings or provider.get_expense_mappings(
            enable_only=True
        )
        instance = next(
            (m for m in expense_mappings if m.key == selected_mapping_key), None
        )
        if instance and instance.payee:
            return instance.payee
        return None

    def get_payee(
        self, data, ownerid, selected_mapping_key: Optional[str] = None
    ):
        provider = get_mapping_provider(ownerid)
        expense_mappings = provider.get_expense_mappings(enable_only=True)
        self.key_list = [m.key for m in expense_mappings]
        self._expense_mappings = expense_mappings
        if data['bill_identifier'] == BILL_WECHAT and data['transaction_type'] == "/" and data['transaction_category'] == "信用卡还款":
            return data['counterparty']
        if data['bill_identifier'] == BILL_WECHAT and data['transaction_type'] == "/":
            return data['payment_method'][:4]
        if data['transaction_category'] == "微信红包（单发）":
            return self.payee[2:]
        if data['transaction_category'] == "转账-退款" or data['transaction_category'] == "微信红包-退款":
            return "退款"
        if '(' in self.payee and ')' in self.payee and self.notes == "Transfer":
            match = re.search(r'\((.*?)\)', self.payee)
            if match:
                return match.group(1)
        if selected_mapping_key:
            mapped = self._payee_from_mapping_key(selected_mapping_key, data, ownerid)
            if mapped:
                return self._finalize_payee(data, mapped)
            return self._finalize_payee(data, self.payee)
        return self.general_payee(data, ownerid)

    def general_payee(self, data, ownerid):
        payee = self.payee
        matching_keys = [k for k in self.key_list if k in self.payee or k in self.notes]
        max_order = None
        for matching_key in matching_keys:
            expense_instance = next((m for m in self._expense_mappings if m.key == matching_key), None)
            matching_max_order = None
            if expense_instance and expense_instance.expend:
                expend_instance_priority = expense_instance.expend.account.count(":") * 100
                payee_instance_priority = 50 if expense_instance.payee else 0
                matching_max_order = expend_instance_priority + payee_instance_priority
            elif expense_instance and expense_instance.payee:
                matching_max_order = 50

            if matching_max_order is not None and (
                    max_order is None or matching_max_order > max_order):
                max_order = matching_max_order

                if expense_instance is not None and expense_instance.payee is not None and expense_instance.payee != '':
                    payee = expense_instance.payee

            elif matching_max_order is not None and (max_order is None or matching_max_order == max_order):
                payee = self.payee

        return self._finalize_payee(data, payee)


def get_shouzhi(data): #TODO
    shouzhi = data['transaction_type']
    high = ""
    loss = "-"

    if shouzhi == "支出":
        if data.get("bill_identifier") == BILL_ALI and data.get("transaction_status") == "退款成功":
            return loss, high
        return high, loss
    elif shouzhi == "收入":
        return loss, high
    elif shouzhi in ["/", "不计收支"]:
        if data['bill_identifier'] == BILL_ALI:
            if data.get("transaction_status") == "退款成功":
                return loss, high
            if data['commodity'] == "信用卡还款":
                return high, loss
            elif alipay_uses_fallback_payment_method(data.get('payment_method')):
                return high, loss
            elif (re.match(pattern["花呗自动还款"], data['commodity'])) or (re.match(pattern["花呗主动还款"], data['commodity'])):
                    return high, loss
            elif data['commodity'] == "余额宝-自动转入" or data['commodity'] == "余额宝-单次转入" or data['commodity'] == "转账收款到余额宝":
                return high, loss
            elif data['commodity'] == "充值-普通充值":
                return high, loss
            elif "放款成功" in  data['transaction_status']:
                return loss, high
            elif "还款成功" in  data['transaction_status']:
                return high, loss
            elif ("买入" in data['commodity']) or ("公司" in data['counterparty']):  #TODO  # 目前没找到更合理的规律
                return high,loss
            else:
                return loss, high
        elif data['bill_identifier'] == BILL_WECHAT and data['transaction_category'] == "信用卡还款":
            return high, loss
        else:
            return loss, high
    else:
        return None, None


def get_attribute(data, attribute_handlers):
    # 如果需要对数据进行一般性处理，也可以在这里完成

    bill_type = data['bill_identifier']
    if bill_type in attribute_handlers:
        return attribute_handlers[bill_type](data)
    return None


def get_uuid(data):
    uuid_handlers = {
        BILL_ALI: alipay_get_uuid,
        BILL_WECHAT: wechatpay_get_uuid,
    }
    return get_attribute(data, uuid_handlers)


def get_status(data):
    status_handlers = {
        BILL_ALI: alipay_get_status,
        BILL_WECHAT: wechatpay_get_status,
        BILL_CMB_CREDIT: cmb_credit_get_status,
        BILL_BOC_DEBIT: boc_debit_get_status,
        BILL_ICBC_DEBIT:icbc_debit_get_status,
        BILL_CCB_DEBIT:ccb_debit_get_status,
    }
    return get_attribute(data, status_handlers)


def get_note(data):
    notes_handlers = {
        BILL_ALI: alipay_get_note,
        BILL_WECHAT: wechatpay_get_note,
        BILL_CMB_CREDIT: cmb_credit_get_note,
        BILL_BOC_DEBIT: boc_debit_get_note,
        BILL_ICBC_DEBIT:icbc_debit_get_note,
        BILL_CCB_DEBIT:ccb_debit_get_note,
    }
    data['commodity'] = data['commodity'].replace('"', '\\"')
    return get_attribute(data, notes_handlers)


def get_amount(data):
    amount_handlers = {
        BILL_ALI: alipay_get_amount,
        BILL_WECHAT: wechatpay_get_amount,
        BILL_CMB_CREDIT: cmb_credit_get_amount,
        BILL_BOC_DEBIT: boc_debit_get_amount,
        BILL_ICBC_DEBIT: icbc_debit_get_amount,
        BILL_CCB_DEBIT:ccb_debit_get_amount,
    }
    return get_attribute(data, amount_handlers)


def get_tag(data):
    tag_handlers = {
        BILL_ALI: alipay_get_tag,
        BILL_WECHAT: wechatpay_get_tag,
    }
    return get_attribute(data, tag_handlers)


def get_balance(data):
    balance_handlers = {
        BILL_BOC_DEBIT: boc_debit_get_balance,
        BILL_ICBC_DEBIT: icbc_debit_get_balance,
        BILL_CCB_DEBIT:ccb_debit_get_balance,
    }
    return get_attribute(data, balance_handlers)


def get_commission(data):
    commission_handlers = {
        BILL_ALI: alipay_get_commission,
        BILL_WECHAT: wechatpay_get_commission,
    }
    return get_attribute(data, commission_handlers)


def get_discount(data):
    tag_handlers = {
        BILL_ALI: alipay_get_discount,
        BILL_WECHAT: wechatpay_get_discount,
    }
    return get_attribute(data, tag_handlers)
