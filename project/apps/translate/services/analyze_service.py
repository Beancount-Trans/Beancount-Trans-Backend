from project.apps.translate.utils import *
from project.apps.translate.views.AliPay import *
from project.apps.translate.views.WeChat import *
from project.apps.translate.views.BOC_Debit import *
from project.apps.translate.views.CMB_Credit import *
from project.apps.translate.views.ICBC_Debit import *
from project.apps.translate.views.CCB_Debit import *
from project.apps.translate.services.pipeline import BillParsingPipeline
from project.apps.translate.services.bill_support import (
    extract_latest_bill_date,
    record_parse_success,
)
from project.apps.translate.services.steps import (
    ConvertToCSVStep,
    InitializeBillStep,
    PreFilterStep,
    ParseStep,
    PostFilterStep,
    FormatStep,
    CacheStep,
    FileWritingStep
)


class AnalyzeService:
    def __init__(self, user: object, config):
        # 接收用户实例，可通过该实例获取用户信息，如owner_id、username等
        self.user = user
        self.owner_id = user.id if hasattr(user, 'id') else None
        self.username = user.username if hasattr(user, 'username') else None
        self.config = config

    def analyze_single_file(self, uploaded_file, args):
        """解析单个文件"""
        # 创建初始上下文
        context = {
            "owner_id": self.owner_id,
            "user": self.user,
            "username": self.username,
            "config": self.config,
            "args": args,
            "uploaded_file": uploaded_file,
            "csv_file_object": None,  # 转换后的CSV数据文件对象
            # <_io.StringIO object at 0x7fd6b0e82440>
            "initialized_bill": [],  # 初始化获取各账单可用的字段（字典列表）
            # [{'transaction_time': '2024-02-25 20:01:48', 'transaction_category': '母婴亲子', 'counterparty': '十月**店', 'commodity': '【天猫U先】十月结晶会员尊享精致妈咪出行必备生活随心包4件套 等多件', 'transaction_type': '/', 'amount': 14.8, 'payment_method': '亲情卡(凯义(王凯义))', 'transaction_status': '交易成功', 'notes': '/', 'bill_identifier': 'alipay', 'uuid': '2024022522001174561439593142', 'discount': False}]
            "parsed_data": [],  # 解析后的数据（包含格式化所需的所有字段以及AI字典信息）
            # [{'date': '2024-02-25', 'time': '20:01:48', 'uuid': '2024022522001174561439593142', 'status': 'ALiPay - 交易成功', 'payee': '十月结晶', 'note': '【天猫U先】十月结晶会员尊享精致妈咪出行必备生活随心包4件套 等多件', 'tag': None, 'balance': None, 'balance_date': '2024-02-26', 'expense': 'Expenses:Shopping:Parent', 'expenditure_sign': '', 'account': 'Equity:OpenBalance', 'account_sign': '-', 'amount': '14.80', 'discount': False, 'currency': 'CNY', 'selected_expense_key': '十月结晶', 'expense_candidates_with_score': [{'key': '等多件', 'score': 0.5471}, {'key': '出行', 'score': 0.557}, {'key': '**', 'score': 0.5499}, {'key': '十月结晶', 'score': 0.5642}], 'actual_prices': '14.80'}]
            "formatted_data": [],  # 格式化结果(FormatStep输出)
            # [{'formatted': '2024-02-25 * "十月结晶" "【天猫U先】十月结晶会员尊享精致妈咪出行必备生活随心包4件套 等多件"\n    time: "20:01:48"\n    uuid: "2024022522001174561439593142"\n    status: "ALiPay - 交易成功"\n    Expenses:Shopping:Parent 14.80 CNY\n    Equity:OpenBalance -14.80 CNY\n\n', 'selected_expense_key': '十月结晶', 'expense_candidates_with_score': [{'key': '等多件', 'score': 0.5432}, {'key': '出行', 'score': 0.5528}, {'key': '**', 'score': 0.5475}, {'key': '十月结晶', 'score': 0.5606}], 'uuid': '2024022522001174561439593142'}
            "status": "pending",  # 状态标识
        }

        # 创建管道
        pipeline = BillParsingPipeline([
            ConvertToCSVStep(),
            InitializeBillStep(),
            PreFilterStep(),
            ParseStep(),
            PostFilterStep(),
            CacheStep(),
            FormatStep(),
            FileWritingStep()  # 仅在需要写入文件时执行
        ])

        # 执行管道
        result_context = pipeline.process(context)
        if result_context.get('status') != 'error' and result_context.get('formatted_data'):
            record_parse_success(
                result_context.get('bill_type'),
                extract_latest_bill_date(result_context),
            )

        return result_context
