from typing import Literal
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
    challenge: str = Field(description="A specific challenge this company or person faces, drawn from the research data")
    evidence_type: Literal["evidenced", "inferred"] = Field(description="'evidenced' = the research data EXPLICITLY reports this problem/complaint/negative review/dispute/adverse news. 'inferred' = the challenge is reasoned from facts that do not themselves state a problem (business model, company stage, role, or a neutral/positive fact like an achievement or growth move). A real, cited fact does NOT make a signal 'evidenced' if the challenge itself is your inference. When unsure, choose 'inferred'.")
    evidence: str = Field(description="For 'evidenced': cite the specific source/fact/data point that reports the problem. For 'inferred': begin with 'Likely based on...' or 'Inferred from...' and name the specific fact the inference is built on.")

class ProspectResearch(BaseModel):
    company_snapshot: CompanySnapshot
    prospect_role: ProspectRole
    pain_signals: list[PainSignal] = Field(description="0-3 pain signals — concrete challenges or strategic concerns the prospect faces. Use evidenced signals when concrete data exists; otherwise provide specific inferred signals (evidence prefixed with 'Likely based on...' / 'Inferred from...'). Return empty only when the data can't support even one grounded signal.")
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

## ENTITY CONFIRMATION — READ BEFORE ANYTHING ELSE
The research data often includes content about DIFFERENT companies or people that share the prospect's name. Same-name collisions are extremely common on review platforms (Glassdoor, Trustpilot, Yelp, Indeed) and in general search — a "Company X reviews" result is frequently a *different* Company X.

A TARGET ENTITY block appears at the top of the research data with the prospect's company, its confirmed website domain (if known), and location. Use it as the source of truth for identity.

- Ignore any search result or extracted page clearly about a different company or a different location, even when the name matches.
- A pain signal may be marked evidence_type "evidenced" ONLY when you can confirm the source is about THIS entity — the confirmed domain appears in/matches the source, the location matches, or there is an unmistakable identifying detail. A shared company NAME is NOT confirmation.
- If the confirmed website domain is "not confirmed", you have NO anchor to verify external reviews or complaints. Do NOT mark any third-party review/complaint/rating/news as "evidenced" — at most treat the underlying concern as "inferred", and record the unverified identity in data_gaps.
- When a review is negative but you cannot confirm it is the same entity, leave it out entirely rather than risk attributing another company's problems to this prospect.

## PAIN SIGNALS — ACCURACY OVER VOLUME
A sales rep uses these signals to open a conversation. Give them a real, usable angle on the prospect — without ever fabricating facts. Two failures are equally bad: inventing concrete problems that don't exist, AND returning nothing when the data clearly supports a grounded inference.

Each signal MUST set its `evidence_type` field, and this choice is strict:
- "evidenced" — ONLY when the research data EXPLICITLY reports this problem: a complaint, negative review, dispute, regulatory action, or adverse news event that is stated in the data. The problem itself must appear in the data, not be reasoned by you
- "inferred" — a challenge you REASONED from facts that do not themselves state a problem: business model, company stage, role scope, market position, or a neutral/positive fact (an achievement, a service the company offers, a growth initiative)
- A real, cited fact does NOT make a signal "evidenced" if the CHALLENGE is your inference. A completed project, a service offered, or an expansion move are not problems — a challenge drawn from them is "inferred". When in doubt, choose "inferred"

### HARD RULES
- No more than 3 pain signals total. There is no fixed minimum
- Lead with "evidenced" signals. When the data reports no explicit problems, provide specific "inferred" signals rather than leaving the prospect blank
- EVERY signal must trace to a specific fact in the research data — a cited source for an "evidenced" signal, or the named fact behind an "inferred" one
- An "inferred" signal MUST begin its evidence field with "Likely based on..." or "Inferred from...". Never present an inference as concrete evidence
- Return an empty list ONLY when the data is too thin to support even one specific, grounded inference

### EVIDENCE CATEGORIES
  Category 1 — CONCRETE: Customer/client complaints, negative reviews on consumer platforms (Trustpilot, Google Reviews, Yell, G2, etc.), regulatory actions, reported incidents, public disputes. Cite the specific source → evidence_type "evidenced"
  Category 2 — NEWS-BASED: Recent layoffs, funding rounds implying cash burn, lawsuits, market exits, product recalls, leadership or regulatory changes. Cite the news item → evidence_type "evidenced"
  Category 3 — INFERENCE: A concrete, role-relevant challenge reasoned from business model, company stage, competitive landscape, or recent initiatives visible in the data → evidence_type "inferred"

### HOW MANY, AND OF WHAT KIND
- Category 1/2 evidence available: report up to 3 signals, prioritising evidenced ones
- No Category 1/2 evidence: report INFERRED signals, each anchored to a DISTINCT, specific fact in the data. Record the absence of concrete evidence in data_gaps
- NEVER add a signal just to reach three. Prefer one or two strong, specific signals over three weak ones — a single well-grounded signal is a better result than padded filler
- Each signal must rest on its OWN distinct fact. Do not split one observation into multiple signals, and do not restate the company's description as a challenge
- Employee reviews about internal conditions are NOT Category 1 evidence unless a direct business impact is reported in news

### FABRICATION TRAPS (do NOT do these)
- INVENTING EVIDENCE: Never cite a review, complaint, or news item that is not actually in the research data. If you did not see the source, the signal is at most INFERRED — label it as such
- REFRAMING POSITIVES AS PAIN: A post celebrating a milestone is NOT evidence of pressure to sustain it. A hiring announcement is NOT evidence of a recruitment problem. Positive signals are positive — do not invert them
- PRODUCT-AS-PAIN: If a company PROVIDES a service, that is their strength, not their pain. A provider's own service area is not a pain signal
- DESCRIBING THEIR BUSINESS AS PAIN: The normal work of their role or industry is a job description, not a pain signal
- GENERIC FILLER: "They care about growth", "they need better tools", "scaling challenges" with no specific fact behind them

## CONFIDENCE SCORING — HARD CALIBRATION
Your confidence score tells a sales rep: "Can I trust this research before I pick up the phone?"

### MANDATORY RULES (not guidelines — these override your judgment)
- Confidence 8-10: REQUIRES category 1 or 2 evidence for at least 2 pain signals AND rich company data from multiple sources. If you don't have this, you CANNOT score 8+
- Confidence 5-7: Some category 1/2 evidence OR strong company data with limited pain signal evidence
- Confidence 3-4: Only category 3 inferences available, or data is thin across the board
- Confidence 1-2: Almost no useful data found

### CALIBRATION CHECK: Before outputting your score, verify
- If data_gaps mentions missing reviews/complaints/news → score MUST be ≤ 6
- If ALL pain signals have evidence_type "inferred" → score MUST be ≤ 5
- If you have 0 pain signals → score MUST be ≤ 4
- Does your score match the evidence quality, or are you being optimistic? Default to LOWER when uncertain

## LINKEDIN DATA (when provided)
- LinkedIn profile and posts data is HIGH-VALUE — it shows verified role, career trajectory, self-described expertise, and topics they publicly care about
- Use the "about" section to understand their professional identity and priorities
- Use post topics and engagement to identify what they're actively thinking about
- Their skills list shows what they want to be known for
- Career history shows trajectory and tenure — a new role means different priorities than someone 3 years in

## COMPANY SIZE & STAGE
- LinkedIn company data (employee count) is the most reliable source for company size — it is standardized and verified. If available, use it as the primary source for size/stage categorization.
- If no LinkedIn company size data is available, use website or external sources with caveats like "per company website" or "not confirmed independently"
- For revenue, ARR, or funding data: only report numbers found in the research data with a specific source citation"""


def _calibrate_confidence(research: dict, entity_confirmed: bool = True) -> int:
    """Deterministic floor on confidence based on evidence quality.

    The model is told to calibrate confidence itself, but gpt-4.1-mini
    routinely over-scores leads that lack concrete evidence — it returns
    7-8 with no verified pain signals. A sales rep trusts that score,
    calls the lead, and has nothing concrete to reference. These caps
    enforce the prompt's own calibration rules regardless of model output:

    - No pain signals at all       -> cap 5 (nothing concrete to act on)
    - Only inferred signals         -> cap 4 (no verified evidence behind them)
    - Entity could not be anchored  -> cap 4 (we can't confirm any "evidenced"
      source is about THIS company vs a same-named one — fail safe)

    Signal type is read from each PainSignal's explicit `evidence_type`
    field ("evidenced" / "inferred"), which structured output forces the
    model to set. `entity_confirmed` is False when no company website domain
    could be identified, so external review/complaint content cannot be
    trusted as the right entity regardless of how the model labelled it.
    """
    score = research["confidence_score"]
    pain_signals = research["pain_signals"]

    if not pain_signals:
        return min(score, 5)

    all_inferred = all(ps["evidence_type"] == "inferred" for ps in pain_signals)
    if all_inferred or not entity_confirmed:
        return min(score, 4)

    return score


def _build_user_prompt(
    name: str,
    company: str,
    email: str | None,
    linkedin: str | None,
    serper_snippets: list[dict],
    jina_extractions: list[dict],
    linkedin_data: dict | None = None,
    target_domain: str | None = None,
    location: str | None = None,
) -> str:
    parts = [
        f"PROSPECT: {name}",
        f"COMPANY: {company}",
    ]
    if email:
        parts.append(f"EMAIL: {email}")
    if linkedin:
        parts.append(f"LINKEDIN: {linkedin}")

    # Identity anchor — the model uses this to reject same-name wrong entities.
    parts.append("\n--- TARGET ENTITY ---")
    parts.append(f"Company: {company}")
    parts.append(f"Confirmed website domain: {target_domain or 'not confirmed'}")
    parts.append(f"Location: {location or 'not confirmed'}")
    parts.append("Attribute reviews, complaints, and news to this prospect ONLY if the content matches this identity.")

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

    target_domain = serper_results.get("_meta", {}).get("target_domain")
    location = (linkedin_data or {}).get("profile", {}).get("location") or None

    user_prompt = _build_user_prompt(
        name, company, email, linkedin, serper_snippets, jina_extractions,
        linkedin_data=linkedin_data,
        target_domain=target_domain,
        location=location,
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
    research_dict["confidence_score"] = _calibrate_confidence(
        research_dict, entity_confirmed=bool(target_domain)
    )

    return {
        "research": research_dict,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }
