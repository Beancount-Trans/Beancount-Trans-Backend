# project/apps/translate/services/similarity.py
import torch
import logging
from pathlib import Path
from typing import List, Dict, Optional, TYPE_CHECKING
from django.conf import settings
from transformers import BertTokenizer, BertModel
from openai import OpenAI

if TYPE_CHECKING:
    from project.apps.translate.services.parse_api_key import ParseLlmConfig

logger = logging.getLogger(__name__)


class SimilarityModel:
    """抽象基类，定义相似度计算接口"""
    def calculate_similarity(self, text: str, candidates: List[str]) -> str:
        raise NotImplementedError


class BertSimilarity(SimilarityModel):
    """BERT相似度计算实现"""
    _tokenizer = None
    _model = None
    _embedding_cache: Dict[str, torch.Tensor] = {}  # 嵌入缓存字典

    @classmethod
    def load_model(cls):
        # 模型加载逻辑（本地/Hugging Face Hub）
        if cls._model is None:
            local_model_path = Path(__file__).parent.parent.parent.parent.parent / "pretrained_models" / "bert-base-chinese"
            try:
                cls._tokenizer = BertTokenizer.from_pretrained(local_model_path)
                cls._model = BertModel.from_pretrained(local_model_path)
            except OSError:
                cls._tokenizer = BertTokenizer.from_pretrained('bert-base-chinese')
                cls._model = BertModel.from_pretrained('bert-base-chinese')
            cls._model.eval()  # 设置为评估模式
        return cls._tokenizer, cls._model

    def _get_embedding(self, text: str) -> torch.Tensor:
        """核心AI操作：生成文本向量表示，带缓存"""
        if text in self._embedding_cache:
            return self._embedding_cache[text]

        # BERT处理流程
        tokenizer, model = self.load_model()
        inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=64)
        with torch.no_grad():  # 禁用梯度计算
            outputs = model(**inputs)
        embedding = outputs.last_hidden_state[:,0,:]  # 获取[CLS]标记的表示作为文本嵌入
        self._embedding_cache[text] = embedding  # 缓存结果
        return embedding

    def calculate_similarity(self, text: str, candidates: List[str]) -> Dict[str, float]:
        """返回每个候选词的相似度分数及最高分条目"""
        text_embed = self._get_embedding(text)  # 获取查询文本嵌入
        scores = {}

        # 计算与每个候选的余弦相似度
        for candidate in candidates:
            cand_embed = self._get_embedding(candidate)
            similarity = torch.cosine_similarity(text_embed, cand_embed, dim=1)
            scores[candidate] = similarity.item()  # 保存相似度分数

        best_match = max(scores.items(), key=lambda x: x[1])[0]  # 找出最佳匹配
        return {"best_match": best_match, "scores": scores}

    def collect_training_data(self, transaction_text, candidates, selected_key):
        """自动收集训练数据"""
        import json
        from datetime import datetime

        data_point = {
            "timestamp": datetime.now().isoformat(),
            "transaction_text": transaction_text,
            "candidates": candidates,
            "selected_key": selected_key
        }

        # 追加到数据文件
        with open("training_data.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(data_point, ensure_ascii=False) + "\n")

        return f"已收集 {len(candidates)} 个样本"


class DeepSeekSimilarity(SimilarityModel):
    """DeepSeek API相似度计算实现"""
    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        enable_realtime: bool = True,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.deepseek.com").rstrip('/')
        self.model = model or "deepseek-chat"
        self.enable_realtime = enable_realtime
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)

    def calculate_similarity(self, text: str, candidates: List[str]) -> dict:
        prompt = f"""请从以下候选列表中选择最匹配文本的条目：
        文本内容：{text}
        候选列表：{", ".join(candidates)}
        只需返回最匹配的候选值，不要任何解释"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            result = response.choices[0].message.content.strip()
            best_match = result if result in candidates else candidates[0]
            # DeepSeek没有分数，全部置为None或0
            scores = {c: (1.0 if c == best_match else 0.0) for c in candidates}
            return {"best_match": best_match, "scores": scores}
        except Exception as e:
            raise ValueError(f"DeepSeek调用失败，请检查API密钥是否正确")


class FallbackSimilarity(SimilarityModel):
    """主模型失败后，本批剩余请求直接回退本地模型。"""

    def __init__(self, primary: SimilarityModel, fallback: SimilarityModel):
        self.primary = primary
        self.fallback = fallback
        self._primary_available = True

    def calculate_similarity(self, text: str, candidates: List[str]) -> dict:
        if self._primary_available:
            try:
                return self.primary.calculate_similarity(text, candidates)
            except Exception as e:
                self._primary_available = False
                logger.warning("解析 LLM 调用失败，回退本地 BERT: %s", e)
        return self.fallback.calculate_similarity(text, candidates)


def build_similarity_model(
    engine: Optional[str],
    llm: Optional['ParseLlmConfig'],
) -> Optional[SimilarityModel]:
    """按解析引擎构建相似度模型；LLM 不可用时回退本地 BERT。"""
    normalized = (engine or 'BERT').strip()
    if normalized == 'None':
        return None
    if normalized in ('Platform', 'DeepSeek'):
        if llm is None:
            logger.warning("解析引擎 %s 未配置可用 LLM，回退本地 BERT", normalized)
            return BertSimilarity()
        primary = DeepSeekSimilarity(
            llm.api_key,
            base_url=llm.base_url,
            model=llm.model,
            timeout=getattr(settings, 'PARSE_LLM_TIMEOUT', 15.0),
        )
        return FallbackSimilarity(primary, BertSimilarity())
    return BertSimilarity()
