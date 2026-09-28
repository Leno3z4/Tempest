from __future__ import annotations
import json
from typing import Any
from pydantic import ValidationError
from .models import AIBatchDecision

SYSTEM_PROMPT = """
You are Tempest's crypto market analyst. You are not the execution engine.
Use only supplied numbers plus clearly identified recent web evidence when search is enabled.
Treat rumors and social posts as unverified. Prefer HOLD when evidence is weak or contradictory.
Never invent price, volume, liquidity, listings, catalysts, or news. A confidence value is not
a probability of profit. For BUY explain setup and invalidation. For SELL explain why risk or
thesis changed. Suggested size is advisory; the deterministic risk engine has final authority.
Return exactly the requested JSON schema.
""".strip()

class GeminiAdvisor:
    def __init__(self, api_key: str, model: str, web_grounding: bool) -> None:
        self.enabled = bool(api_key)
        self.model = model
        self.web_grounding = web_grounding
        self.client: Any = None
        if self.enabled:
            from google import genai
            self.client = genai.Client(api_key=api_key)

    def analyze(self, market_state: dict[str, Any]) -> AIBatchDecision:
        if not self.enabled:
            return AIBatchDecision(decisions=[])
        from google.genai import types
        tools = [types.Tool(google_search=types.GoogleSearch())] if self.web_grounding else None
        prompt = ("Analyze the candidates and open positions. Return BUY, HOLD, or SELL for each. "
                  "For BUY, suggested_quote_usd must be conservative; HOLD/SELL can use 0.\n\n"
                  "MARKET STATE:\n" + json.dumps(market_state, separators=(",", ":"), ensure_ascii=False))
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AIBatchDecision,
            tools=tools,
            max_output_tokens=4000,
        )
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
        if getattr(response, "parsed", None) is not None:
            try:
                return AIBatchDecision.model_validate(response.parsed)
            except ValidationError:
                pass
        return AIBatchDecision.model_validate_json(getattr(response, "text", ""))
