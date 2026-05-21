from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from app.config import get_settings


# ── Structured Output Schema ──
# OpenAI enforces this at token generation level — guaranteed valid JSON.

class CompanySnapshot(BaseModel):
    what_they_do: str = Field(description="2-3 sentence description of what the company does, who they serve (target market), and how they deliver value")
    industry: str = Field(description="The company's industry or niche (e.g., 'Legal Technology', 'Healthcare SaaS', 'E-commerce Platform', 'B2B Marketing Agency'). Be specific — 'Technology' alone is too broad")
    key_offerings: list[str] = Field(description="The company's main products, services, or solutions (2-5 items). Each item should be a short phrase, not a sentence")
    size_and_stage: str = Field(description="Company size, stage, or growth phase. Say 'not confirmed' if unknown")

class ProspectRole(BaseModel):
    likely_priorities: str = Field(description="What this person likely cares about based on their title and company")
    key_responsibilities: str = Field(description="Key responsibilities and pressures in their role")

class PainSignal(BaseModel):
    challenge: str = Field(description="A specific, concrete challenge this company or person faces — backed by evidence from the research data")
    evidence: str = Field(description="Direct evidence from the research data that supports this pain signal. Must cite a specific source, fact, or data point")

class ProspectResearch(BaseModel):
    company_snapshot: CompanySnapshot
    prospect_role: ProspectRole
    pain_signals: list[PainSignal] = Field(description="0-3 pain signals. ONLY include pain signals backed by concrete evidence from the research data. If no real pain signals are found, return an empty list.")
    confidence_score: int = Field(description="1-10 rating of how confident you are in this research. 1=mostly guessing, 10=rich verified data")
    data_gaps: list[str] = Field(description="List what information was missing or couldn't be verified")


# ── System Prompt ──

SYSTEM_PROMPT = """You are a B2B prospect research analyst. Your job is to analyze research data about a prospect and produce an accurate, evidence-based intelligence profile.

Your output is PURE PROSPECT INTELLIGENCE — factual information about who this person is, what their company does, and what challenges they face. You are NOT generating outreach copy, personalization hooks, or recommended angles. Someone else will use your research to decide how to approach this prospect.

The prospect could be in ANY industry — healthcare, SaaS, logistics, finance, manufacturing, consulting, or anything else. Adapt your analysis to their specific sector, company type, and role.

RULES:
- Only use information present in the provided research data
- If something cannot be verified from the data, say "not confirmed" — never guess or fabricate
- Ignore any research content that is clearly about a different person or company
- Be specific — tailor every insight to THIS prospect's actual industry, role, and situation
- Generic filler like "they care about growth" or "they value innovation" is useless — tie every point to concrete evidence

COMPANY SNAPSHOT:
- "what_they_do" should be 2-3 sentences: what they do + who they serve + how they deliver value. Not a tagline — real detail from the research
- "industry" must be specific: "Healthcare Revenue Cycle Management" not just "Healthcare", "E-commerce Fulfillment" not just "Logistics"
- "key_offerings" should list their actual products/services found in the research — not generic capabilities. If their website lists specific service names or product tiers, use those

PAIN SIGNALS — CRITICAL:
- ONLY output a pain signal if there is direct, concrete evidence in the research data
- If the research data does not reveal any real pain points, return an EMPTY pain_signals list — this is correct behavior, not a failure
- Never fabricate pain signals from general industry assumptions
- "Inferred from industry trends" is NOT valid evidence — you need something specific to THIS company
- Consider the company's own services: if a company PROVIDES growth consulting, "struggles with growth" is not a valid pain signal — that is their service offering, not their problem
- Each pain signal must cite a specific fact, quote, data point, or observation from the research

CONFIDENCE SCORING:
- 8-10: Multiple verified sources with rich, specific data about the prospect and company
- 4-7: Some data found but with notable gaps or thin sources
- 1-3: Very little data available, mostly surface-level information
- When data is thin, score LOW and list the gaps — honesty is more valuable than false confidence

LINKEDIN DATA (when provided):
- LinkedIn profile and posts data is HIGH-VALUE — it shows verified role, career trajectory, self-described expertise, and topics they publicly care about
- Use the "about" section to understand their professional identity and priorities
- Use post topics and engagement to identify what they're actively thinking about
- Their skills list shows what they want to be known for
- Career history shows trajectory and tenure — a new role means different priorities than someone 3 years in"""


def _build_user_prompt(
    name: str,
    company: str,
    email: str | None,
    linkedin: str | None,
    serper_snippets: list[dict],
    jina_extractions: list[dict],
    linkedin_data: dict | None = None,
) -> str:
    parts = [
        f"PROSPECT: {name}",
        f"COMPANY: {company}",
    ]
    if email:
        parts.append(f"EMAIL: {email}")
    if linkedin:
        parts.append(f"LINKEDIN: {linkedin}")

    # Add LinkedIn profile + posts data (when available)
    if linkedin_data:
        profile = linkedin_data.get("profile", {})
        posts = linkedin_data.get("posts", [])

        if profile:
            parts.append("\n--- LINKEDIN PROFILE ---")
            if profile.get("headline"):
                parts.append(f"Headline: {profile['headline']}")
            if profile.get("about"):
                parts.append(f"About: {profile['about']}")
            if profile.get("job_title"):
                parts.append(f"Current Title: {profile['job_title']}")
            if profile.get("company_name"):
                parts.append(f"Company: {profile['company_name']} ({profile.get('company_industry', '')}, {profile.get('company_size', '')} employees)")
            if profile.get("total_experience_years"):
                parts.append(f"Total Experience: {profile['total_experience_years']} years")
            if profile.get("experiences"):
                parts.append("Career History:")
                for exp in profile["experiences"]:
                    status = "current" if exp.get("still_working") else f"ended {exp.get('ended', '?')}"
                    parts.append(f"  • {exp['title']} at {exp['company']} (started {exp.get('started', '?')}, {status})")
            if profile.get("skills"):
                parts.append(f"Skills: {', '.join(profile['skills'][:10])}")

        if posts:
            parts.append("\n--- LINKEDIN POSTS (recent activity) ---")
            for i, post in enumerate(posts[:5], 1):
                parts.append(f"\nPost {i} ({post.get('relative_date', post.get('date', ''))}):")
                parts.append(f"  Text: {post['text'][:500]}")
                parts.append(f"  Engagement: {post.get('total_reactions', 0)} reactions, {post.get('comments', 0)} comments, {post.get('reposts', 0)} reposts")

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
    linkedin_data: dict | None = None,
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
        name, company, email, linkedin, serper_snippets, jina_extractions,
        linkedin_data=linkedin_data,
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
