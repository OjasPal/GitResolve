"""Bounded execution feedback, not model training or unbounded self-retry."""
import hashlib
import json


def fingerprint(candidate):
    parsed=getattr(candidate,"parsed",candidate)
    text=parsed.model_dump_json() if hasattr(parsed,"model_dump_json") else str(getattr(candidate,"raw_text",parsed))
    try:
        data=json.loads(text)
        # Rewording an explanation is not a different implementation strategy.
        keys={"edits","tests","reproduction_diff","final_diff","diff"}
        if isinstance(data,dict) and keys.intersection(data): data={k:v for k,v in data.items() if k in keys}
        text=json.dumps(data,sort_keys=True)
    except (ValueError,TypeError): pass
    return hashlib.sha256(text.encode()).hexdigest()


def category(message):
    text=message.lower()
    if "scope" in text or "changed lines" in text: return "scope"
    if "match original" in text or "anchor" in text: return "anchor"
    if "protected" in text or "unsafe" in text or "unsupported file operation" in text: return "protected"
    if "regression" in text or "reproduce" in text: return "regression"
    if "quality review" in text or "requirement" in text: return "review"
    if "test suite" in text or "failed" in text: return "tests"
    return "format"


STRATEGIES={
    "scope":"Use fewer, smaller source edits and concise meaningful tests. Do not omit required behavior, empty existing files, or change limits. Report blocked_reason if the minimum safe change cannot fit.",
    "anchor":"Re-read the actual original source. Use a short unique exact anchor; do not reconstruct Unicode or spacing from memory.",
    "protected":"This operation is not allowed. Do not disguise it as another edit or try to bypass policy.",
    "regression":"Choose a different behavioral assertion that fails on the original for the requested reason. Avoid collection/import errors and mocks that hide the implementation.",
    "tests":"Address the first concrete failure and its cause. Change the implementation/test strategy instead of repeating the failed proposal.",
    "review":"Address the specific unmet requirement with implementation and real evidence. Never relabel incomplete checks as passed.",
    "format":"Return valid structured output with exact original source anchors; report blocked_reason when the request cannot be safely completed."
}


class AttemptMemory:
    def __init__(self,records=()): self.records=list(records)[-6:]
    def record(self,candidate,message):
        feedback=message if len(message)<=2200 else message[:1000]+"\n…\n"+message[-1200:]
        item={"fingerprint":fingerprint(candidate),"category":category(message),"feedback":feedback}
        self.records=(self.records+[item])[-6:]
        return item
    def repeated(self,candidate): return any(r["fingerprint"]==fingerprint(candidate) for r in self.records)
    def persistent(self): return len(self.records)>=2 and self.records[-1]["category"]==self.records[-2]["category"]
    def prompt(self):
        if not self.records:return "No previous failures."
        return json.dumps(self.records,ensure_ascii=True)+"\nRequired strategy change: "+STRATEGIES[self.records[-1]["category"]]+" Never repeat an identical rejected proposal."
