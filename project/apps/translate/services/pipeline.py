# project/apps/translate/services/pipeline.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any
import logging


logger = logging.getLogger(__name__)

class Step(ABC):
    """解析管道步骤的基类"""
    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤逻辑，处理输入数据并返回结果

        Args:
        context (Dict[str, Any]): 上下文信息，包含管道执行的相关配置和状态
        Returns:        Dict[str, Any]: 输出数据，包含当前步骤的处理结果
        Raises:         NotImplementedError: 如果子类未实现此方法
        例如：
            {
                "status": "success",
                "data": {...},
                "errors": []
            }
        这里的 "status" 可以是 "success" 或 "error"，"data"
        可以包含当前步骤处理的结果，"errors" 列表可以包含任何错误信息。
        """
        pass

    def _error(self, context: Dict, message: str, code: str = 'PARSE_FAILED') -> Dict:
        """统一错误处理"""
        context.setdefault('errors', []).append(f"{self.__class__.__name__}: {message}")
        context['error_code'] = code
        context['status'] = 'error'
        logger.error(message)
        return context


class BillParsingPipeline:
    """账单解析管道

    通过传入多个PipelineStep依次执行解析流程
    """
    def __init__(self, steps: List[Step]):
        self.steps = steps

    def process(self, context: Dict) -> Dict:
        """执行管道流程

        Args:
            initial_context (Dict): 初始上下文数据

        Returns:
            Dict: 处理完成后的最终上下文数据
        """
        for step in self.steps:
            if context['status'] == 'error':
                break
            context = step.execute(context)
        return context
