from __future__ import annotations
import json
from typing import Any
from pydantic import ValidationError
from .models import AIBatchDecision

SYSTEM_PROMPT = """
You are Tempest's crypto market analyst. You are not the execution engine.
Use only supplied numbers plus clearly identified recent web evidence when search is enabled.
Treat rumors, social posts, promotions, anonymous claims, and influencer statements as unverified.
Never invent price, liquidity, volume, listings, catalysts, or news.
A confidence value and moonshot_score are model assessments, not probabilities of profit.
A "100x" upside case is a scenario, never a forecast or a reason by itself to buy.
For BUY explain the setup, evidence, invalidation, and why the token could outperform.
For SELL explain what changed or what exit condition was reached.
Return HOLD when evidence is insufficient or the setup is already too extended.
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
        prompt = (
            "Analyze the candidates and open positions. Return BUY, HOLD, or SELL for each. "
            "For BUY, suggested_quote_usd must be conservative and trade_style should be MOONSHOT "
            "only when the supplied market evidence supports asymmetric upside and the setup is not "
            "already fully extended. upside_case_multiple is a scenario estimate, not a probability.\n\n"
            "MARKET STATE:\n" + json.dumps(market_state, separators=(",", ":"), ensure_ascii=False)
        )
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
