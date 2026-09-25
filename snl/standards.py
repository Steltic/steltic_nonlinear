"""standards.py -- what every SNL agent shares when it asks the standards: the retrieval policy and the tool.

The policy is steltic_grokbot skills/Skill_querying_PACKAGED.md, the same one HR Steel and CFS follow:
ONE document per call, an EXACT id when the provision is known, full text only to navigate to an id.
The tool applies it to whatever the model sends (snl/rag.py policy_plan) and records the form it sent.
`review` writes the engineer's review with it; `collect` fills the component parameter file with it.
"""
from __future__ import annotations

from . import rag

RETRIEVAL_POLICY = """===== RETRIEVAL POLICY (mandatory -- how every search_engineering_standards call is written) =====
This is the query policy the Query file manager is built for (steltic_grokbot skills/Skill_querying_PACKAGED.md),
the same policy HR Steel and CFS follow. The tool applies it to whatever you send and records the form it
sent; write it that way yourself.

1. ONE document per call, by its key: document="ASCE7" | "ASCE41" | "A342" | "A341" | "A360" | "A358".
   Never search all documents blindly. System -> component -> action -> method -> the one document that
   governs: Chapter 16 acceptance is ASCE 7-22; component models and BPON are ASCE 41-23; modelling
   parameters and acceptance criteria for steel components are AISC 342-22; detailing is AISC 341-22.
2. EXACT ID WHEN KNOWN. type="exact_section" | "exact_equation" | "exact_table", query = the id ALONE:
     {"type":"exact_section","document":"ASCE7","query":"16.4.1.2","purpose":"mean drift limit"}
     {"type":"exact_equation","document":"ASCE7","query":"16.4-1","purpose":"force-controlled action"}
     {"type":"exact_table","document":"A342","query":"C3.6","purpose":"column modelling parameters"}
   Not a sentence. Not the document name. Not "ASCE 7-22 Equation 16.4-1 for force-controlled actions".
3. FULL TEXT ONLY TO NAVIGATE: type="fts", query = the standard's own printed words, one idea, no
   sentence, no ids mixed in ("mean story drift ratio limit", not "what drift is allowed in Chapter 16").
   Read the ids it returns, then ask for them EXACTLY in the next call.
4. One provision per call: provision, definition, equation, limits, table, procedure -- each its own call.
   For every equation you compute from, also fetch its "where:" variables (context_neighbors=1), its
   applicability and its exceptions.
5. Waves: (1) navigation + the core provisions -> (2) definitions, limits and the cross-references the
   results name -> (3) verification of every factor that entered a number you quote.
6. Commentary ids carry a C- prefix; want_commentary stays false unless you need intent, and a commentary
   excerpt never supplies a design value.
7. Search only the documents listed under DOCUMENTS IN THE CORPUS. A document listed as ABSENT was never
   converted on this PC: do not search it (such a call is answered with a corpus-gap note, is not counted,
   and is wasted); cite it from memory, marked (UNVERIFIED), and say in section 8 that it was unavailable.
8. A miss is escalated for you: the exact id, the clause/chapter filter dropped, a reworded query, then
   every other document. When the result says another document answered, cite THAT document, not the one
   you asked for.
9. Never invent an id. NO PASSAGES after the ladder is an honest answer: re-word ONCE in the standard's own
   terms or ask for the parent section; do not fill the gap from memory without marking it (UNVERIFIED).
   Every clause you cite as verified comes from a passage returned this session -- cite document, section,
   eq/table id and the printed page the passage gives.
10. Search as often as the review needs. There is no reward for searching less; an uncited clause you
   could have looked up is the failure, not an extra call.
"""

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_engineering_standards",
        "description": ("Search the licensed standards corpus for the clause, table or equation you are about to cite. "
                        "ONE provision, ONE document per call. When you know the id, ask for it EXACTLY: type=\"exact_section\" | "
                        "\"exact_equation\" | \"exact_table\" with `query` = the id alone (\"16.4.1.2\", \"16.4-1\", \"12.12-1\") -- not a "
                        "sentence, not the document name. Use type=\"fts\" only to NAVIGATE to an id, with `query` in the standard's own "
                        "printed words, one idea, no ids mixed in; read the ids it returns and ask for them exactly next. Search only "
                        "documents the corpus holds (see DOCUMENTS IN THE CORPUS). A miss is escalated for you (exact id, filters dropped, "
                        "reworded, other documents) and the result says which document answered. Cite only what a passage supports; a "
                        "clause the search cannot find is cited from memory and marked (UNVERIFIED)."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The id alone for an exact type; otherwise the standard's own printed words, one idea"},
                "document": {"type": "string", "enum": list(rag.DOCUMENTS), "description": "; ".join("%s = %s" % (k, d) for k, (_c, d) in rag.DOCUMENTS.items())},
                "type": {"type": "string", "enum": ["exact_section", "exact_equation", "exact_table", "id", "fts", "keyword"],
                         "description": "How to look it up. Exact types take the id alone in `query`. fts/keyword navigate."},
                "clause": {"type": "string", "description": "Exact clause / table / equation id, when you did not put it in `query`"},
                "chapter": {"type": "string", "description": "Restrict a navigation query to one chapter, e.g. 16 or F"},
                "purpose": {"type": "string", "description": "Why you need it, short -- recorded in the transcript"},
                "want_commentary": {"type": "boolean", "description": "Commentary instead of the provision. Default false; commentary never supplies a design value."},
                "context_neighbors": {"type": "integer", "minimum": 0, "maximum": 2,
                                      "description": "Surrounding chunks to include. Use 1 for an equation, to get its \"where:\" variables."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query", "document"],
        },
    },
}]
