from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from app.config import get_settings


# ── Structured Output Schema ──
# OpenAI enforces this at token generation level — guaranteed valid JSON.

class CompanySnapshot(BaseModel):
    what_they_do: str = Field(description="One-sentence description of what the company does")
    size_and_stage: str = Field(description="Company size, stage, or growth phase. Say 'not confirmed' if unknown")
    recent_news: str = Field(description="Recent news, funding, hires, or changes in last 6 months. Say 'not confirmed' if nothing found")

class ProspectRole(BaseModel):
    likely_priorities: str = Field(description="What this person likely cares about based on their title and company")
    key_responsibilities: str = Field(description="Key responsibilities and pressures in their role")

class PainSignal(BaseModel):
    challenge: str = Field(description="A specific challenge someone in this role/industry faces")
    evidence: str = Field(description="What in the research data supports this. Say 'inferred from industry' if no direct evidence")

class PersonalizationHook(BaseModel):
    hook: str = Field(description="One specific, relevant thing to reference in an opening email")
    why_it_matters: str = Field(description="Why this matters to them right now")

class OutreachAngle(BaseModel):
    recommended_angle: str = Field(description="The best angle to approach this prospect for a cold outreach")
    talking_points: list[str] = Field(description="2-3 specific talking points based on research")

class ProspectResearch(BaseModel):
    company_snapshot: CompanySnapshot
    prospect_role: ProspectRole
    pain_signals: list[PainSignal] = Field(description="2-3 specific pain signals")
    personalization_hook: PersonalizationHook
    outreach_angle: OutreachAngle
    confidence_score: int = Field(description="1-10 rating of how confident you are in this research. 1=mostly guessing, 10=rich verified data")
    data_gaps: list[str] = Field(description="List what information was missing or couldn't be verified")


# ── System Prompt ──

SYSTEM_PROMPT = """You are a B2B sales research analyst. Your job is to analyze research data about a prospect and produce a structured intelligence profile for personalized outreach.

The prospect could be in ANY industry — healthcare, SaaS, logistics, finance, manufacturing, consulting, or anything else. Adapt your analysis to their specific sector, company type, and role. Do not assume any particular business model.

RULES:
- Only use information present in the provided research data
- If something cannot be verified from the data, say "not confirmed" — never guess or fabricate
- Ignore any research content that is clearly about a different person or company
- Be specific and actionable — tailor every insight to THIS prospect's actual industry, role, and situation
- Generic filler like "they care about growth" or "they value innovation" is useless — tie every point to concrete evidence from the research
- Pain signals must be specific to their industry, role, and company stage — not broad business clichés
- The personalization hook must reference something concrete from the research (a specific service, news event, initiative, or statement) — not a generic observation
- Confidence score reflects data richness: 8-10 = multiple verified sources, 4-7 = some data with gaps, 1-3 = mostly inferred"""


def _build_user_prompt(
    name: str,
    company: str,
    email: str | None,
    linkedin: str | None,
    serper_snippets: list[dict],
    jina_extractions: list[dict],
) -> str:
    parts = [
        f"PROSPECT: {name}",
        f"COMPANY: {company}",
    ]
    if email:
        parts.append(f"EMAIL: {email}")
    if linkedin:
        parts.append(f"LINKEDIN: {linkedin}")

    # Add search snippets
    parts.append("\n--- SEARCH RESULTS ---")
    for item in serper_snippets:
        query_label = item.get("source_query", item.get("label", ""))
        parts.append(f"\n[{query_label}]")
        for result in item.get("results", []):
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            if title or snippet:
                parts.append(f"• {title}: {snippet}")

    # Add extracted web content
    parts.append("\n--- EXTRACTED WEB CONTENT ---")
    for ext in jina_extractions:
        if ext.get("error") or not ext.get("content"):
            continue
        url = ext.get("url", "")
        title = ext.get("title", "")
        content = ext.get("content", "")
        parts.append(f"\n[Source: {title or url}]")
        parts.append(content)

    parts.append("\n--- END OF RESEARCH DATA ---")
    parts.append("\nAnalyze the research data above and produce the prospect intelligence profile.")

    return "\n".join(parts)


async def synthesize_lead(
    name: str,
    company: str,
    email: str | None,
    linkedin: str | None,
    serper_results: dict,
    jina_extractions: list[dict],
) -> dict:
    """Send all research data to OpenAI and get structured prospect profile."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not set in environment.")

    # Build snippets list from serper results
    serper_snippets = []
    for query_key, query_data in serper_results.items():
        serper_snippets.append({
            "source_query": query_key,
            "results": query_data.get("results", []),
        })

    user_prompt = _build_user_prompt(
        name, company, email, linkedin, serper_snippets, jina_extractions
    )

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    response = await client.beta.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format=ProspectResearch,
        temperature=0.3,
    )

    result = response.choices[0].message.parsed

    return {
        "research": result.model_dump(),
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }
