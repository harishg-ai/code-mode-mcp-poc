# Accuracy eval: one tool per endpoint vs Code Mode
Run at 2026-11-22T05:59:18+00:00 · model `nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter · temperature 0 · 4 questions · max 15 turns each

Same questions, same model, same seeded API. Answers are checked against ground truth computed from the seed data.

## Summary

| Mode | Tools | Correct | Avg tool calls | Avg LLM calls | Avg input tokens | Avg output tokens | Total input tokens | Total output tokens |
|---|---|---|---|---|---|---|---|---|
| naive | 82 | 3/4 | 5.2 | 6.0 | 78,443 | 1,713 | 313,773 | 6,853 |
| codemode | 2 | 4/4 | 5.2 | 6.2 | 13,742 | 1,280 | 54,968 | 5,121 |

Input tokens are the provider-reported prompt tokens summed over every model call, so they include tool definitions, history and tool results.

## Per question

| # | Question | Expected | Mode | Answer | Result | Tool calls | Input tokens | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | How many tasks have status "done"? | 75 | naive | 75 | PASS | 1 | 24,934 | 129 |
| 1 | How many tasks have status "done"? | 75 | codemode | 75 | PASS | 3 | 4,656 | 365 |
| 3 | What is the total amount of all invoices whose status is "overdue"? | 50269 | naive | 50269 | PASS | 2 | 34,158 | 678 |
| 3 | What is the total amount of all invoices whose status is "overdue"? | 50269 | codemode | 50269 | PASS | 3 | 4,457 | 552 |
| 6 | Which user has the most overdue high-priority tasks? A task is overdue if its due_date is before 2026-11-21 and its status is not "done". An | ["user name 18"] | naive | "user name 18" | PASS | 3 | 62,323 | 3,988 |
| 6 | Which user has the most overdue high-priority tasks? A task is overdue if its due_date is before 2026-11-21 and its status is not "done". An | ["user name 18"] | codemode | "user name 18" | PASS | 5 | 19,335 | 2,023 |
| 9 | What is the combined MRR of all distinct customers that have at least one invoice with status "overdue"? | 43352 | naive | null | FAIL: no answer after 15 turns | 15 | 192,358 | 2,058 |
| 9 | What is the combined MRR of all distinct customers that have at least one invoice with status "overdue"? | 43352 | codemode | 43352 | PASS | 10 | 26,520 | 2,181 |
