from __future__ import annotations

import json
import os
import re
from typing import Any
from dotenv import load_dotenv
import httpx2

load_dotenv()


class OpenRouterLLM:
    """LLM Agent client utilizing OpenRouter API with Qwen: Qwen3 8B."""

    def __init__(self) -> None:
        load_dotenv()
        self.api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.model = os.getenv("OPENROUTER_MODEL", "qwen/qwen3-8b").strip()
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and (self.api_key.startswith("sk-or-") or self.api_key.startswith("sk-")))

    async def analyze_case(
        self,
        case: dict[str, Any],
        evidence_summary: dict[str, Any],
        policy_rules: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Sử dụng Qwen3 8B để phân tích ngữ nghĩa của case và đối soát với bằng chứng MCP."""
        if not self.is_configured:
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://day09.vinaction.local",
            "X-Title": "Day09 L3B Multi-Agent Qwen3",
            "Content-Type": "application/json",
        }

        system_instruction = (
            "Bạn là AI Chuyên gia Điều tra Khiếu nại Thương mại điện tử (E-commerce Dispute Investigation Agent). "
            "Nhiệm vụ của bạn là phân tích khiếu nại của khách hàng, đối chiếu với các bằng chứng thực tế từ hệ thống "
            "(đơn hàng, lịch sử vận chuyển, thanh toán) và các quy tắc chính sách (policy rules). "
            "Bạn PHẢI trả về duy nhất một đối tượng JSON hợp lệ, không chứa văn bản ngoài hay định dạng markdown."
        )

        user_prompt = f"""Hãy phân tích case sau và đưa ra kết luận:

[1. YÊU CẦU CỦA KHÁCH HÀNG]
{json.dumps(case.get("customer_request", {}), ensure_ascii=False, indent=2)}

[2. BẰNG CHỨNG THỰC TẾ THU THẬP TỪ MCP GATEWAY]
{json.dumps(evidence_summary, ensure_ascii=False, indent=2)}

[3. DANH SÁCH LUẬT CHÍNH SÁCH (POLICY RULES)]
{json.dumps(list(policy_rules.keys()), ensure_ascii=False, indent=2)}

YÊU CẦU ĐẦU RA:
Trả về DUY NHẤT một chuỗi JSON chuẩn có cấu trúc sau:
{{
  "primary_issue": "<Chọn chính xác một trong các luật trong danh sách policy rules>",
  "confidence": <Số thực từ 0.8 đến 1.0>,
  "reasoning": "<Giải thích ngắn gọn 1-2 câu về cơ sở phán quyết dựa trên bằng chứng>"
}}
"""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 150,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx2.AsyncClient(timeout=45.0) as client:
                response = await client.post(self.base_url, headers=headers, json=payload)
                if response.status_code == 200:
                    result = response.json()
                    choices = result.get("choices", [])
                    if not choices:
                        return None
                    content = choices[0].get("message", {}).get("content", "")
                    if not content or not isinstance(content, str):
                        return None

                    cleaned = content.strip()
                    if "```" in cleaned:
                        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
                        if match:
                            cleaned = match.group(1).strip()

                    parsed = None
                    try:
                        parsed = json.loads(cleaned)
                    except Exception:
                        match = re.search(r"\{[\s\S]*\}", cleaned)
                        if match:
                            try:
                                parsed = json.loads(match.group(0))
                            except Exception:
                                pass

                    if isinstance(parsed, str):
                        try:
                            nested = json.loads(parsed)
                            if isinstance(nested, dict):
                                parsed = nested
                        except Exception:
                            pass

                    if isinstance(parsed, dict):
                        return parsed
                    return None
                else:
                    print(f"[OpenRouter LLM Warning] HTTP {response.status_code}: {response.text}")
                    return None
        except Exception as exc:
            print(f"[OpenRouter LLM Error] {exc}")
            return None
