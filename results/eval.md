# Accuracy eval: one tool per endpoint vs Code Mode
Run at 2026-11-24T03:15:19+00:00 · model `nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter · temperature 0 · 12 questions · max 15 turns each

Same questions, same model, same seeded API. Answers are checked against ground truth computed from the seed data.

## Summary

| Mode | Tools | Correct | Avg tool calls | Avg LLM calls | Avg input tokens | Avg output tokens | Total input tokens | Total output tokens |
|---|---|---|---|---|---|---|---|---|
| naive | 82 | 11/12 | 3.8 | 4.7 | 69,247 | 1,099 | 830,963 | 13,187 |
| codemode | 2 | 12/12 | 5.2 | 6.2 | 11,870 | 1,000 | 142,439 | 11,996 |

Input tokens are the provider-reported prompt tokens summed over every model call, so they include tool definitions, history and tool results.

## Per question

| # | Question | Expected | Mode | Answer | Result | Tool calls | Input tokens | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | How many tasks have status "done"? | 75 | naive | 75 | PASS | 1 | 24,934 | 129 |
| 1 | How many tasks have status "done"? | 75 | codemode | 75 | PASS | 3 | 4,656 | 365 |
| 2 | How many customers are on the enterprise tier? | 11 | naive | 11 | PASS | 1 | 22,189 | 99 |
| 2 | How many customers are on the enterprise tier? | 11 | codemode | 11 | PASS | 5 | 6,931 | 604 |
| 3 | What is the total amount of all invoices whose status is "overdue"? | 50269 | naive | 50269 | PASS | 2 | 34,158 | 678 |
| 3 | What is the total amount of all invoices whose status is "overdue"? | 50269 | codemode | 50269 | PASS | 3 | 4,457 | 552 |
| 4 | Which customer has the highest MRR? Answer with the customer name. | ["customer name 18"] | naive | "customer name 18" | PASS | 1 | 23,197 | 783 |
| 4 | Which customer has the highest MRR? Answer with the customer name. | ["customer name 18"] | codemode | "customer name 18" | PASS | 3 | 4,600 | 634 |
| 5 | How many tickets are open with severity sev1? | 4 | naive | 4 | PASS | 1 | 21,994 | 106 |
| 5 | How many tickets are open with severity sev1? | 4 | codemode | 4 | PASS | 5 | 7,473 | 565 |
| 6 | Which user has the most overdue high-priority tasks? A task is overdue if its due_date is before 2026-11-21 and its status is not "done". An | ["user name 18"] | naive | "user name 18" | PASS | 3 | 62,323 | 3,988 |
| 6 | Which user has the most overdue high-priority tasks? A task is overdue if its due_date is before 2026-11-21 and its status is not "done". An | ["user name 18"] | codemode | "user name 18" | PASS | 5 | 19,335 | 2,023 |
| 7 | Which project has the most tasks? Answer with the project name. | ["project name 5"] | naive | "project name 5" | PASS | 11 | 243,492 | 966 |
| 7 | Which project has the most tasks? Answer with the project name. | ["project name 5"] | codemode | "project name 5" | PASS | 4 | 7,403 | 1,065 |
| 8 | How many firewall rules are enabled and have action "block"? | 1 | naive | 1 | PASS | 1 | 21,879 | 124 |
| 8 | How many firewall rules are enabled and have action "block"? | 1 | codemode | 1 | PASS | 3 | 4,429 | 428 |
| 9 | What is the combined MRR of all distinct customers that have at least one invoice with status "overdue"? | 43352 | naive | null | FAIL: no answer after 15 turns | 15 | 192,358 | 2,058 |
| 9 | What is the combined MRR of all distinct customers that have at least one invoice with status "overdue"? | 43352 | codemode | 43352 | PASS | 10 | 26,520 | 2,181 |
| 10 | How many tasks are assigned to members of the team with id 1? | 87 | naive | 87 | PASS | 6 | 129,590 | 3,994 |
| 10 | How many tasks are assigned to members of the team with id 1? | 87 | codemode | 87 | PASS | 14 | 45,197 | 2,505 |
| 11 | What is the email address of the owner of project 3? | "user email 9" | naive | "user email 9" | PASS | 2 | 32,916 | 159 |
| 11 | What is the email address of the owner of project 3? | "user email 9" | codemode | "user email 9" | PASS | 4 | 5,180 | 455 |
| 12 | How many DNS records have type CNAME? | 2 | naive | 2 | PASS | 1 | 21,933 | 103 |
| 12 | How many DNS records have type CNAME? | 2 | codemode | 2 | PASS | 4 | 6,258 | 619 |
