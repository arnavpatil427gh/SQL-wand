"""
Centralized prompt templates for the SQL Assistant.
All prompts are kept explicit and concise for small models.
"""

SYSTEM_PROMPT = """You are a helpful and concise SQL mentor for a student learning databases.
Ground all your explanations and SQL queries strictly on the provided schema tables and columns.
Do NOT invent tables, columns, or relations that are not in the schema.
Always keep answers clear, educational, and to the point.
"""

EXPLAIN_PROMPT_TEMPLATE = """Explain the following SQL query in plain, simple language for a beginner learner:

```sql
{sql}
```

Relevant Database Schema:
{schema_text}

Guidelines:
1. State the overall purpose of the query in 1-2 clear sentences.
2. Break down the execution step-by-step (e.g., FROM/JOIN, WHERE filtering, GROUP BY/aggregates, SELECT columns, ORDER/LIMIT).
3. Mention any performance or logical takeaways in simple terms.
Keep the explanation focused and readable.
"""

FIX_PROMPT_TEMPLATE = """The user ran the following SQL query and encountered an error:

```sql
{sql}
```

Error details:
- Message: {error_message}
- Character position: {error_position}
- Hint: {error_hint}

Relevant Database Schema:
{schema_text}

Task:
1. Explain concisely why the error happened (1-2 sentences).
2. Provide the corrected SQL query.

Rules:
- The corrected query MUST be provided in a SINGLE fenced ```sql ... ``` code block.
- Place ONLY the corrected SQL inside that ```sql code block (no comments or surrounding explanations inside the code block).
- Only use valid table and column names from the provided schema.
"""
