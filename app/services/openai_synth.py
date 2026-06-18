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
    pain_signals: list[PainSignal] = Field(description="0-3 pain signals — challenges, pressures, or strategic concerns this prospect faces. Use direct evidence when available, reasonable inferences when not. Empty list is valid when data doesn't support any.")
    confidence_score: int = Field(description="1-10 rating of how confident you are in this research. 1=mostly guessing, 10=rich verified data")
    data_gaps: list[str] = Field(description="List what information was missing or couldn't be verified")


# ── System Prompt ──

SYSTEM_PROMPT = """You are a B2B prospect research analyst. Your job is to analyze research data about a prospect and produce an accurate, evidence-based intelligence profile.

Your output is PURE PROSPECT INTELLIGENCE — factual information about who this person is, what their company does, and what challenges they face. You are NOT generating outreach copy, personalization hooks, or recommended angles. Someone else will use your research to decide how to approach this prospect.

The prospect could be in ANY industry — healthcare, SaaS, logistics, finance, manufacturing, consulting, or anything else. Adapt your analysis to their specific sector, company type, and role.

## RULES
- Only use information present in the provided research data
- If something cannot be verified from the data, say "not confirmed" — never guess or fabricate
- Ignore any research content that is clearly about a different person or company
- Be specific — tailor every insight to THIS prospect's actual industry, role, and situation
- Generic filler like "they care about growth" or "they value innovation" is useless — tie every point to concrete evidence
- EVIDENCE INTEGRITY: When citing evidence, ONLY name sources (websites, platforms, publications) that explicitly appear in the search results or extracted content provided. Never name a review platform, news outlet, or data source that isn't in the data — citing a source you didn't see is fabrication

## COMPANY SNAPSHOT
- "what_they_do" should be 2-3 sentences: what they do + who they serve + how they deliver value. Not a tagline — real detail from the research
- Look for sub-brands, subsidiaries, or related brands mentioned in the research (e.g., "Company X also operates Brand Y and Brand Z"). Include these in what_they_do if found
- "industry" must be specific: "Healthcare Revenue Cycle Management" not just "Healthcare", "E-commerce Fulfillment" not just "Logistics"
- "key_offerings" should list their actual products/services found in the research — not generic capabilities. If their website lists specific service names or product tiers, use those
- Capture differentiators — if reviews or content mention unique selling points (e.g., "no-deposit tenancy", "same-day delivery", "free guarantor service"), include those as offerings, not just generic service categories

## PAIN SIGNALS — ACCURACY IS EVERYTHING
A sales rep will use these pain signals to open a conversation. A WRONG or FABRICATED pain signal destroys credibility instantly. Returning 0 pain signals with honest data gaps is a CORRECT output. Returning 3 manufactured signals is a CRITICAL FAILURE.

### HARD RULES
- Only report pain signals you can back with specific evidence FROM the research data
- Return an empty list when the data does not support any pain signals — this is expected and correct
- Maximum 3 pain signals. There is NO minimum. Do not stretch to fill slots
- EVERY pain signal MUST cite a specific source, fact, or data point in the evidence field

### EVIDENCE CATEGORIES (determines what you can report)
  Category 1 — CONCRETE: Customer/tenant/client complaints, negative reviews on consumer platforms (Trustpilot, Google Reviews, Yell, allAgents, WhichPad, G2, etc.), regulatory actions, reported incidents, public disputes. Quote or cite the specific source
  Category 2 — NEWS-BASED: Recent layoffs, funding rounds implying cash burn, lawsuits, market exits, product recalls, leadership changes, regulatory changes. Cite the news item
  Category 3 — INFERENCE: Inferences from business model, competitive landscape, or industry trends. ALWAYS prefix the evidence field with "Likely based on..." or "Inferred from..."

### CATEGORY LIMITS (enforced strictly)
- If you have category 1 or 2 evidence: report up to 3 pain signals
- If you ONLY have category 3 inferences (no concrete or news evidence at all): report 0 or 1 pain signals MAX. Flag the lack of concrete evidence in data_gaps
- Employee reviews (Indeed, Glassdoor) about internal conditions are NOT category 1. Only use if they reveal direct business impact reported in news

### FABRICATION TRAPS (do NOT do these)
- REFRAMING POSITIVES AS PAIN: A LinkedIn post celebrating "100% occupancy!" is NOT evidence of "pressure to maintain occupancy." A hiring post saying "We're expanding!" is NOT evidence of "recruitment challenges." Positive signals are positive — do not invert them
- PRODUCT-AS-PAIN: If a company PROVIDES a service, that is their strength, not their pain. Property management company → "property management complexity" is NOT a valid pain signal
- DESCRIBING THEIR BUSINESS AS PAIN: "Managing diverse tenant needs" when that IS their job is not a pain signal — it's a job description
- GENERIC FILLER: "They care about growth", "they need better tools", "scaling challenges" with no specific evidence

## CONFIDENCE SCORING — HARD CALIBRATION
Your confidence score tells a sales rep: "Can I trust this research before I pick up the phone?"

### MANDATORY RULES (not guidelines — these override your judgment)
- Confidence 8-10: REQUIRES category 1 or 2 evidence for at least 2 pain signals AND rich company data from multiple sources. If you don't have this, you CANNOT score 8+
- Confidence 5-7: Some category 1/2 evidence OR strong company data with limited pain signal evidence
- Confidence 3-4: Only category 3 inferences available, or data is thin across the board
- Confidence 1-2: Almost no useful data found

### CALIBRATION CHECK: Before outputting your score, verify
- If data_gaps mentions missing reviews/complaints/news → score MUST be ≤ 6
- If ALL pain signals are category 3 inferences → score MUST be ≤ 4
- If you have 0 pain signals → score MUST be ≤ 5
- Does your score match the evidence quality, or are you being optimistic? Default to LOWER when uncertain

## LINKEDIN DATA (when provided)
- LinkedIn profile and posts data is HIGH-VALUE — it shows verified role, career trajectory, self-described expertise, and topics they publicly care about
- Use the "about" section to understand their professional identity and priorities
- Use post topics and engagement to identify what they're actively thinking about
- Their skills list shows what they want to be known for
- Career history shows trajectory and tenure — a new role means different priorities than someone 3 years in"""


def _calibrate_confidence(research: dict) -> int:
    """Safety net: cap confidence when ALL pain signals are inferred.

    gpt-4.1-mini sometimes generates category 3 inferences ("Likely
    based on...", "Inferred from...") and still scores 7-8. That's
    misleading — a sales rep trusts the score, calls the lead, and
    has nothing concrete to reference. This caps that one scenario.
    """
    score = research["confidence_score"]
    pain_signals = research["pain_signals"]

    if pain_signals:
        all_inferred = all(
            ps["evidence"].lower().startswith(("likely based on", "inferred from"))
            for ps in pain_signals
        )
        if all_inferred:
            score = min(score, 4)

    return score


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
                settings = get_settings()
                parts.append(f"Skills: {', '.join(profile['skills'][:settings.linkedin_max_skills_for_ai])}")

        if posts:
            settings = get_settings()
            parts.append("\n--- LINKEDIN POSTS (recent activity) ---")
            for i, post in enumerate(posts[:settings.linkedin_max_posts_for_ai], 1):
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
    relevant_snippets: list[dict] | None = None,
) -> dict:
    """Send all research data to OpenAI and get structured prospect profile."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not set in environment.")

    # Use relevance-filtered snippets when available (strips wrong-company results).
    # Falls back to raw serper results for backwards compatibility.
    if relevant_snippets:
        snippets_by_query: dict[str, list] = {}
        for r in relevant_snippets:
            key = r["source_query"]
            if key not in snippets_by_query:
                snippets_by_query[key] = []
            snippets_by_query[key].append({
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
            })
        serper_snippets = [
            {"source_query": key, "results": results}
            for key, results in snippets_by_query.items()
        ]
    else:
        serper_snippets = []
        for query_key, query_data in serper_results.items():
            if query_key.startswith("_"):
                continue
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
        temperature=settings.openai_temperature,
    )

    result = response.choices[0].message.parsed
    research_dict = result.model_dump()
    research_dict["confidence_score"] = _calibrate_confidence(research_dict)

    return {
        "research": research_dict,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }
