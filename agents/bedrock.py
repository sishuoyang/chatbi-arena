from dataclasses import dataclass
import boto3


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens)


def ZeroUsage() -> Usage:
    return Usage(0, 0)


@dataclass
class ConverseResult:
    text: str
    usage: Usage


class BedrockClient:
    def __init__(self, region: str):
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def converse(self, model_id: str, system: str, messages: list[dict],
                 inference: dict) -> ConverseResult:
        """messages: [{"role": "user"|"assistant", "content": [{"text": "..."}]}]"""
        kwargs = dict(modelId=model_id, messages=messages,
                      inferenceConfig=inference)
        if system:
            kwargs["system"] = [{"text": system}]
        resp = self._client.converse(**kwargs)
        parts = resp["output"]["message"]["content"]
        text = "".join(p.get("text", "") for p in parts)
        u = resp.get("usage", {})
        usage = Usage(input_tokens=u.get("inputTokens", 0),
                      output_tokens=u.get("outputTokens", 0))
        return ConverseResult(text=text, usage=usage)


def cost_usd(usage: Usage, price_in_per_1m: float, price_out_per_1m: float) -> float:
    return (usage.input_tokens / 1_000_000.0) * price_in_per_1m + \
           (usage.output_tokens / 1_000_000.0) * price_out_per_1m
